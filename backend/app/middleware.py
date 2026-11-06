"""Middleware ASGI: request-id, límite de body, rate limiting, cabeceras de seguridad, métricas y access log."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid

from app.config import settings
from app.observability import metrics, request_id_var, user_id_var
from app.security import decode_claims

log = logging.getLogger("jobhunter.access")
_RID_OK = re.compile(r"^[A-Za-z0-9._\-]{8,64}$")
API_PREFIX = "/api/v1"
_EXEMPT = ("/health", "/ready", "/metrics")
_DOCS = ("/docs", "/redoc", "/openapi.json")


# ------------------------------------------------------------------ token bucket
class RateLimiter:
    """Token bucket en memoria. `rate` = tokens por minuto, `burst` = capacidad."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._b: dict[str, tuple[float, float]] = {}      # clave -> (tokens, última vez)
        self._last_gc = clock()

    def allow(self, key: str, rate_per_min: float, burst: float) -> tuple[bool, int]:
        """(permitido, segundos hasta el siguiente token)."""
        now = self._clock()
        per_s = rate_per_min / 60.0
        with self._lock:
            tokens, ts = self._b.get(key, (float(burst), now))
            tokens = min(float(burst), tokens + (now - ts) * per_s)
            if tokens >= 1:
                self._b[key] = (tokens - 1, now)
                ok, wait = True, 0
            else:
                self._b[key] = (tokens, now)
                ok, wait = False, int((1 - tokens) / per_s) + 1 if per_s > 0 else 60
            if now - self._last_gc > 300:                  # purga claves inactivas
                self._last_gc = now
                self._b = {k: v for k, v in self._b.items() if now - v[1] < 600}
        return ok, wait

    def reset(self) -> None:
        with self._lock:
            self._b.clear()


rate_limiter = RateLimiter()


class BodyTooLarge(Exception):
    pass


def client_ip(scope) -> str:
    if settings.trust_proxy_headers:
        for k, v in scope.get("headers", []):
            if k == b"x-forwarded-for":
                first = v.decode("latin-1").split(",")[0].strip()
                if first:
                    return first
    c = scope.get("client")
    return c[0] if c else "unknown"


def _security_headers(path: str) -> list[tuple[bytes, bytes]]:
    if not settings.security_headers:
        return []
    h = [(b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"),
         (b"referrer-policy", b"no-referrer"),
         (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
         (b"cross-origin-resource-policy", b"same-site")]
    if not path.startswith(_DOCS):
        h.append((b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"))
        h.append((b"cache-control", b"no-store"))
    if settings.hsts:
        h.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
    return h


class AppMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        headers = {k: v for k, v in scope.get("headers", [])}
        path = scope["path"]
        method = scope["method"]
        incoming = headers.get(b"x-request-id", b"").decode("latin-1")
        rid = incoming if _RID_OK.match(incoming) else uuid.uuid4().hex
        rid_tok = request_id_var.set(rid)
        uid_tok = user_id_var.set(None)
        ip = client_ip(scope)
        scope.setdefault("state", {})["client_ip"] = ip
        scope["state"]["request_id"] = rid

        state = {"status": 500, "started": False}
        t0 = time.perf_counter()
        extra = _security_headers(path)

        async def send_wrapped(message):
            if message["type"] == "http.response.start":
                state["status"], state["started"] = message["status"], True
                hdrs = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"]
                hdrs.append((b"x-request-id", rid.encode()))
                present = {k.lower() for k, _ in hdrs}
                hdrs += [(k, v) for k, v in extra if k not in present]
                message = {**message, "headers": hdrs}
            await send(message)

        try:
            # --- identidad (solo firma del JWT; la validación contra BD la hace la dependencia)
            auth = headers.get(b"authorization", b"").decode("latin-1")
            uid = None
            if auth[:7].lower() == "bearer ":
                claims = decode_claims(auth[7:].strip())
                if claims:
                    try:
                        uid = int(claims["sub"])
                    except (KeyError, ValueError):
                        uid = None
            if uid is not None:
                user_id_var.set(uid)

            # --- tamaño del body
            clen = headers.get(b"content-length")
            if clen and clen.isdigit() and int(clen) > settings.max_body_bytes:
                await self._reject(send_wrapped, 413, "El cuerpo de la petición es demasiado grande")
                return

            # --- rate limiting
            if settings.rate_limit_enabled and path not in _EXEMPT and method != "OPTIONS":
                ok, wait, which = self._check_rate(path, ip, uid)
                if not ok:
                    metrics.inc("jh_rate_limited_total", scope=which)
                    log.warning("rate limit", extra={"scope": which, "path": path, "ip": ip})
                    await self._reject(send_wrapped, 429, "Demasiadas peticiones, inténtalo más tarde",
                                       [(b"retry-after", str(wait).encode())])
                    return

            received = 0

            async def receive_counted():
                nonlocal received
                msg = await receive()
                if msg["type"] == "http.request":
                    received += len(msg.get("body", b""))
                    if received > settings.max_body_bytes:
                        raise BodyTooLarge()
                return msg

            metrics_inflight(+1)
            try:
                await self.app(scope, receive_counted, send_wrapped)
            except BodyTooLarge:
                if not state["started"]:
                    await self._reject(send_wrapped, 413, "El cuerpo de la petición es demasiado grande")
            finally:
                metrics_inflight(-1)
        except Exception:
            log.exception("error no controlado")
            if not state["started"]:
                await self._reject(send_wrapped, 500, "Error interno")
        finally:
            dur = time.perf_counter() - t0
            route = scope.get("route")
            tpl = getattr(route, "path", None) or ("unmatched" if state["status"] in (404, 405) else "other")
            if tpl[:1] == "/" and path.startswith(API_PREFIX) and not tpl.startswith(API_PREFIX):
                tpl = API_PREFIX + tpl             # los sub-routers guardan la ruta sin el prefijo
            if path in _EXEMPT:
                tpl = path
            metrics.inc("jh_http_requests_total", method=method, path=tpl, status=str(state["status"]))
            metrics.observe("jh_http_request_duration_seconds", dur, method=method, path=tpl)
            if path != "/health":
                log.info("%s %s %s", method, path, state["status"],
                         extra={"method": method, "path": path, "status": state["status"], "ms": round(dur * 1000, 1), "ip": ip})
            request_id_var.reset(rid_tok)
            user_id_var.reset(uid_tok)

    @staticmethod
    def _check_rate(path: str, ip: str, uid: int | None) -> tuple[bool, int, str]:
        s = settings
        if "/auth/" in path:
            ok, w = rate_limiter.allow(f"auth:{ip}", s.rate_auth_per_min, s.rate_auth_burst)
            return ok, w, "auth"
        ok, w = rate_limiter.allow(f"ip:{ip}", s.rate_ip_per_min, s.rate_ip_burst)
        if not ok:
            return False, w, "ip"
        if uid is not None:
            ok, w = rate_limiter.allow(f"user:{uid}", s.rate_user_per_min, s.rate_user_burst)
            if not ok:
                return False, w, "user"
        return True, 0, ""

    @staticmethod
    async def _reject(send, status: int, detail: str, extra_headers: list | None = None):
        body = json.dumps({"detail": detail}, ensure_ascii=False).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json; charset=utf-8"),
                                (b"content-length", str(len(body)).encode()), *(extra_headers or [])]})
        await send({"type": "http.response.body", "body": body})


_in_flight = 0
_in_flight_lock = threading.Lock()


def metrics_inflight(delta: int) -> None:
    global _in_flight
    with _in_flight_lock:
        _in_flight += delta


metrics.gauge_fn("jh_http_in_flight", lambda: _in_flight)


def reset_state() -> None:
    rate_limiter.reset()
