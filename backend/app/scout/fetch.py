"""Acceso HTTP vía Scrapling para TODO el scraping.

Capas (de dentro hacia fuera):
  _transport      -> la única función que hace I/O real (Fetcher rápido o StealthyFetcher/navegador). Los tests la sustituyen.
  fetch_text      -> rate limit por dominio, caché corta, reintentos con backoff, detección de bloqueo y
                     escalado automático Fetcher -> StealthyFetcher.
  get_html/get_json/resolve_redirect -> API que usan las fuentes. Los tests de las fuentes sustituyen estas.

Solo se consultan páginas públicas / guest: no se evaden logins ni se usan credenciales."""
import atexit
import hashlib
import json
import logging
import random
import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode, urljoin, urlsplit

from scrapling.fetchers import Fetcher

from app.config import settings
from app.scout import proxies
from app.scout.ratelimit import breaker, host_of, limiter

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BACKOFF_BASE = 1.0           # s; 1, 2, 4 ... (+ jitter)
BACKOFF_MAX = 20.0
CACHE_TTL = 120.0            # s: evita repetir peticiones idénticas dentro de una búsqueda
CACHE_MAX = 400
RETRY_STATUS = {429, 500, 502, 503, 504}
BLOCK_STATUS = {401, 403, 407, 429}

_sleep = time.sleep          # indirección para tests

breaker.base, breaker.cap = settings.breaker_cooldown_s, settings.breaker_cooldown_max_s


class FetchError(RuntimeError):
    def __init__(self, url: str, status: int | None = None, msg: str = ""):
        self.url, self.status = url, status
        super().__init__((f"{url} -> HTTP {status}" + (f": {msg}" if msg else "")) if status else f"{url} -> {msg}")


class NotFound(FetchError):
    """404/410: la oferta ya no existe (se usa para marcar ofertas cerradas)."""


class Blocked(FetchError):
    """429/403/captcha o circuito abierto: la fuente nos bloquea (distinto de 'sin resultados')."""


# ---------------------------------------------------------------- eventos por hilo (para distinguir blocked / empty / ok)
_ev = threading.local()


def reset_events() -> None:
    _ev.d = {"requests": 0, "ok": 0, "blocked": 0, "errors": 0, "last_blocked": ""}


def events() -> dict:
    """Contadores de las peticiones hechas por ESTE hilo desde `reset_events()` (cada fuente corre en un hilo)."""
    if not hasattr(_ev, "d"):
        reset_events()
    return dict(_ev.d)


def _note(kind: str, detail: str = "") -> None:
    if not hasattr(_ev, "d"):
        reset_events()
    _ev.d[kind] += 1
    _ev.d["requests"] += 1
    if kind == "blocked":
        _ev.d["last_blocked"] = detail[:200]


@dataclass
class Resp:
    status: int
    text: str
    headers: dict[str, str] = field(default_factory=dict)
    url: str = ""


# ---------------------------------------------------------------- detección de bloqueo
BLOCK_MARKERS = (
    "captcha", "cf-challenge", "just a moment", "attention required", "access denied", "authenticating...",
    "verify you are human", "unusual traffic", "are you a robot", "request blocked", "pardon our interruption",
    "enable javascript and cookies",
)


def looks_blocked(status: int, text: str, expect: str = "html") -> bool:
    """403/401/captcha o una respuesta vacía donde se esperaba contenido."""
    if status in BLOCK_STATUS:
        return True
    body = (text or "").strip()
    if expect == "json":
        return False
    if len(body) < 200:
        return True
    if len(body) < 80_000:   # las páginas reales grandes mencionan "captcha" en sus scripts: no cuentan
        head = body[:6000].lower()
        return any(m in head for m in BLOCK_MARKERS)
    return False


# ---------------------------------------------------------------- transporte real
_tls = threading.local()
_sessions: set = set()
_browser_slots = threading.BoundedSemaphore(2)    # máx. 2 navegadores vivos a la vez (cada uno pesa ~200 MB)
_sessions_lock = threading.Lock()


def _stealth_session(timeout: int, proxy: str | None = None):
    s = getattr(_tls, "session", None)
    if s is not None and getattr(_tls, "session_proxy", None) != proxy:
        close_session()                      # el navegador está atado a su proxy: cambiar de salida = sesión nueva
        s = None
    if s is None:
        from scrapling.fetchers import StealthySession
        _tls.slot = _browser_slots.acquire(timeout=180)   # si no hay hueco en 3 min se sigue igualmente
        kw = {"proxy": proxies.browser_proxy(proxy)} if proxy else {}
        s = StealthySession(headless=True, network_idle=True, timeout=timeout * 1000, **kw)
        s.start()
        _tls.session, _tls.session_proxy = s, proxy
        with _sessions_lock:
            _sessions.add(s)
    return s


def close_session() -> None:
    """Cierra el navegador del hilo actual (llamar al terminar la búsqueda de una fuente)."""
    s = getattr(_tls, "session", None)
    if s is not None:
        _tls.session, _tls.session_proxy = None, None
        with _sessions_lock:
            _sessions.discard(s)
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass
        if getattr(_tls, "slot", False):
            _tls.slot = False
            _browser_slots.release()


@atexit.register
def _close_all() -> None:
    with _sessions_lock:
        sessions, _ = list(_sessions), _sessions.clear()
    for s in sessions:
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass


def _decode(page) -> str:
    body = page.body
    if isinstance(body, bytes):
        return body.decode(getattr(page, "encoding", None) or "utf-8", errors="replace")
    return str(body)


def _transport(url: str, *, method: str = "GET", headers: dict | None = None, json_body=None,
               timeout: int = 30, stealth: bool = False, follow_redirects: bool = True, proxy: str | None = None) -> Resp:
    if stealth:
        try:
            page = _stealth_session(timeout, proxy).fetch(url)
        except Exception:  # noqa: BLE001 — el navegador pudo morir: se recrea una vez
            close_session()
            page = _stealth_session(timeout, proxy).fetch(url)
        return Resp(page.status, page.html_content, {k.lower(): v for k, v in dict(page.headers or {}).items()}, str(page.url))
    kw: dict = {"timeout": timeout, "stealthy_headers": True, "follow_redirects": follow_redirects}
    if headers:
        kw["headers"] = headers
    if proxy:
        kw["proxy"] = proxy
    page = Fetcher.post(url, json=json_body, **kw) if method == "POST" else Fetcher.get(url, **kw)
    return Resp(page.status, _decode(page), {k.lower(): v for k, v in dict(page.headers or {}).items()}, str(page.url))


# ---------------------------------------------------------------- caché
_cache: dict[str, tuple[float, Resp]] = {}
_cache_lock = threading.Lock()
STATS = {"requests": 0, "cache_hits": 0, "retries": 0, "escalations": 0}


_prefer_stealth: dict[str, float] = {}    # host -> hasta cuándo ir directo a navegador (el Fetcher rápido ya fue bloqueado y el navegador no)
PREFER_STEALTH_TTL = 900.0


def reset_cache() -> None:
    with _cache_lock:
        _cache.clear()
        _prefer_stealth.clear()


def _cache_key(method: str, url: str, body, stealth: bool) -> str:
    b = hashlib.sha1(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:12] if body is not None else ""
    return f"{method}|{url}|{b}"


def _cache_get(key: str) -> Resp | None:
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.monotonic():
            STATS["cache_hits"] += 1
            return hit[1]
        _cache.pop(key, None)
    return None


def _cache_put(key: str, resp: Resp, ttl: float) -> None:
    if ttl <= 0:
        return
    with _cache_lock:
        if len(_cache) >= CACHE_MAX:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[: CACHE_MAX // 4]:
                _cache.pop(k, None)
        _cache[key] = (time.monotonic() + ttl, resp)


# ---------------------------------------------------------------- petición con todo
def _retry_after(resp: Resp) -> float | None:
    v = resp.headers.get("retry-after", "")
    return min(float(v), BACKOFF_MAX) if re.fullmatch(r"\d+(\.\d+)?", v or "") else None


def _backoff(attempt: int, resp: Resp | None = None) -> float:
    wait = (_retry_after(resp) if resp else None) or min(BACKOFF_MAX, BACKOFF_BASE * 2 ** attempt)
    return wait + random.uniform(0, 0.25 * wait)


def _pick_proxy(host: str, stealth: bool) -> str | None:
    pool = proxies.get_pool()
    if not pool.enabled:
        return None
    if stealth:                          # el navegador es caro de relanzar: se queda con su proxy mientras esté sano
        cur = getattr(_tls, "session_proxy", None)
        if cur and pool.available(cur):
            return cur
    return pool.pick(host)


def _blocked(url: str, status: int | None, msg: str, host: str, *, hard: bool = False, retry_after: float | None = None) -> Blocked:
    """Registra el bloqueo (breaker + evento por hilo) y devuelve la excepción lista para lanzar."""
    if hard:
        cooldown = breaker.failure(host, retry_after)
        limiter.slow_down(host)
        msg = f"{msg}; circuito abierto {cooldown:.0f}s"
    else:
        breaker.soft_failure(host)
    _note("blocked", f"{host} HTTP {status}" if status else f"{host} {msg}")
    return Blocked(url, status, msg)


def fetch_text(url: str, *, method: str = "GET", params: dict | None = None, json_body=None, headers: dict | None = None,
               stealth: bool = False, timeout: int = 30, escalate: bool = True, ttl: float | None = None,
               expect: str = "html") -> Resp:
    """Descarga `url` con rate limit, caché, proxies, reintentos, circuit breaker y escalado a navegador si detecta bloqueo.
    Lanza NotFound (404/410), Blocked (429/403/captcha/circuito abierto) o FetchError (otros >= 400 / red)."""
    if params:
        url = f"{url}{'&' if '?' in url else '?'}{urlencode(params, doseq=True)}"
    key = _cache_key(method, url, json_body, stealth)
    if (cached := _cache_get(key)) is not None:
        return cached

    host = host_of(url)
    if (wait := breaker.check(host)) > 0:
        _note("blocked", f"{host}: circuito abierto")
        raise Blocked(url, 429, f"circuito abierto en {host}: se reanuda en {wait:.0f}s")

    if escalate and not stealth and _prefer_stealth.get(host, 0.0) > time.monotonic():
        stealth = True                   # no se repite el intento rápido que ya sabemos que bloquean: ahorra una petición por página
        timeout = max(timeout, 45)
    resp: Resp | None = None
    last_err = ""
    pool = proxies.get_pool()
    for attempt in range(MAX_ATTEMPTS):
        limiter.acquire(url)
        STATS["requests"] += 1
        proxy = _pick_proxy(host, stealth)
        try:
            resp = _transport(url, method=method, headers=headers, json_body=json_body, timeout=timeout, stealth=stealth, proxy=proxy)
            last_err = ""
        except Exception as e:  # noqa: BLE001 — red, DNS, timeout, túnel del proxy
            resp, last_err = None, str(e)[:200]
        if proxy:
            pool.report(proxy, resp is not None and resp.status not in (407, 429) and resp.status < 600)
            if resp is None and stealth:
                close_session()          # su proxy falló: la próxima sesión elegirá otro
        if resp is not None and resp.status not in RETRY_STATUS:
            break
        if attempt < MAX_ATTEMPTS - 1:
            STATS["retries"] += 1
            _sleep(_backoff(attempt, resp))

    if resp is None:
        _note("errors")
        raise FetchError(url, None, last_err or "sin respuesta")

    if resp.status in (404, 410):
        raise NotFound(url, resp.status)

    if resp.status == 429:               # límite de ritmo: no es un bot-wall, un navegador no lo arregla -> breaker + cooldown
        raise _blocked(url, 429, "demasiadas peticiones (429)", host, hard=True, retry_after=_retry_after(resp))

    # bloqueo -> un solo escalado a navegador sigilante (solo páginas públicas, sin login)
    if escalate and not stealth and looks_blocked(resp.status, resp.text, expect):
        log.info("posible bloqueo en %s (HTTP %s): escalando a StealthyFetcher", url, resp.status)
        STATS["escalations"] += 1
        try:
            out = fetch_text(url, method="GET", headers=headers, stealth=True, timeout=max(timeout, 45), escalate=False, ttl=ttl, expect=expect)
            _prefer_stealth[host] = time.monotonic() + PREFER_STEALTH_TTL
            return out
        except Blocked:
            raise
        except FetchError as e:
            if resp.status >= 400:
                raise FetchError(url, resp.status, f"bloqueado y el escalado falló ({e})") from e
            raise
    if resp.status >= 400:
        if resp.status in BLOCK_STATUS:
            raise _blocked(url, resp.status, f"HTTP {resp.status}", host)
        _note("errors")
        raise FetchError(url, resp.status)
    if stealth and expect == "html" and looks_blocked(resp.status, resp.text, expect):
        _blocked(url, resp.status, "captcha o página vacía incluso con navegador", host)      # se anota; se devuelve igual
    elif breaker.success(host):
        limiter.recover(host)
        log.info("circuito de %s cerrado: se reanuda", host)
    _note("ok")
    _cache_put(key, resp, CACHE_TTL if ttl is None else ttl)
    return resp


# ---------------------------------------------------------------- API para las fuentes
def get_html(url: str, stealth: bool = False, timeout: int = 30) -> str:
    return fetch_text(url, stealth=stealth, timeout=timeout).text


def get_xml(url: str, timeout: int = 30) -> str:
    return fetch_text(url, timeout=timeout, expect="xml", escalate=False).text


def get_json(url: str, params: dict | None = None, *, method: str = "GET", json_body=None, headers: dict | None = None,
             timeout: int = 30):
    h = {"Accept": "application/json", **({"Content-Type": "application/json"} if method == "POST" else {}), **(headers or {})}
    resp = fetch_text(url, method=method, params=params, json_body=json_body, headers=h, timeout=timeout, expect="json", escalate=False)
    try:
        return json.loads(resp.text)
    except json.JSONDecodeError as e:
        raise FetchError(url, resp.status, f"respuesta no es JSON ({resp.text[:80]!r})") from e


def resolve_redirect(url: str, max_hops: int = 4, timeout: int = 20) -> str:
    """Sigue manualmente las redirecciones (sin descargar el destino) y devuelve la URL final.
    Sirve para resolver el 'aplicar en el sitio de la empresa' de Indeed (applystart -> ATS de la empresa)."""
    seen, origin = {url}, _root(url)
    for _ in range(max_hops):
        limiter.acquire(url)
        resp = _transport(url, headers=None, timeout=timeout, follow_redirects=False)
        loc = resp.headers.get("location")
        if resp.status not in (301, 302, 303, 307, 308) or not loc:
            return url
        nxt = urljoin(url, loc)
        if nxt in seen:
            return url
        seen.add(nxt)
        url = nxt
        if urlsplit(url).scheme not in ("http", "https") or _root(url) != origin:
            break               # ya salió del portal: ese es el destino real, no hace falta descargarlo
    return url


def _root(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return ".".join(host.split(".")[-2:])
