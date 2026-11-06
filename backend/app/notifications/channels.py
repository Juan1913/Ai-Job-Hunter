"""Canales de salida OPCIONALES, sin dependencias pesadas (httpx y smtplib). Cada uno recibe el aviso ya compuesto y devuelve el detalle de la entrega.

  webhook   POST JSON firmado: `X-JobHunter-Signature: sha256=HMAC(secreto, "<timestamp>.<cuerpo>")` + `X-JobHunter-Timestamp` (anti-repetición).
  ntfy      POST de texto a `<servidor>/<tema>` (ntfy.sh o uno propio); token opcional como Bearer.
  telegram  Bot API `sendMessage` con el token del propio usuario y su chat_id.
  email     SMTP del operador (JH_SMTP_*), SIEMPRE a la dirección de la cuenta del usuario (no hay destinatarios libres: no sirve de relé de spam).

Las URLs que da el usuario pasan por `assert_public_url` (SSRF) al guardarlas y OTRA VEZ justo antes de enviar; no se siguen redirecciones.
Riesgo residual documentado: entre la comprobación y la conexión un DNS malicioso podría cambiar (rebinding); por eso no se siguen redirecciones y
el cuerpo enviado es solo el aviso."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import smtplib
import time
from email.message import EmailMessage
from urllib.parse import quote, urlsplit

import httpx

from app.config import settings
from app.scout.netguard import UnsafeURL, assert_public_url

KINDS = ("webhook", "ntfy", "telegram", "email")


class DeliveryError(RuntimeError):
    """Fallo de entrega; `retry=False` cuando reintentar no ayuda (URL insegura, configuración rota)."""

    def __init__(self, msg: str, retry: bool = True, code: int | None = None):
        super().__init__(msg)
        self.retry, self.code = retry, code


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, timestamp: str, body: bytes, header: str, tolerance_s: int = 300, now: float | None = None) -> bool:
    """Lo que debe hacer el receptor del webhook (se ofrece aquí para documentarlo y probarlo)."""
    if abs((now or time.time()) - int(timestamp)) > tolerance_s:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), header)


def _scrub(text: str, secret: str | None) -> str:
    """Quita secretos de los mensajes de error (httpx incluye la URL, que en Telegram lleva el token)."""
    t = text
    if secret:
        t = t.replace(secret, "***")
    return re.sub(r"/bot[^/\s]+/", "/bot***/", t)[:300]


def check_url(url: str) -> str:
    try:
        return assert_public_url(url)
    except UnsafeURL as e:
        raise DeliveryError(f"URL no permitida: {e}", retry=False)


def validate_config(kind: str, config: dict, user_email: str | None = None) -> dict:
    """Valida y normaliza la configuración NO secreta de un canal al crearlo/editarlo. Lanza ValueError con un mensaje para el usuario."""
    if kind not in KINDS:
        raise ValueError(f"canal desconocido: {kind}")
    if kind == "webhook":
        url = str(config.get("url", "")).strip()
        _safe_user_url(url)
        return {"url": url}
    if kind == "ntfy":
        server = str(config.get("server") or "https://ntfy.sh").strip().rstrip("/")
        topic = str(config.get("topic", "")).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", topic):
            raise ValueError("topic de ntfy: 4-64 caracteres (letras, números, - y _); usa uno largo e impredecible")
        _safe_user_url(server)
        return {"server": server, "topic": topic}
    if kind == "telegram":
        chat = str(config.get("chat_id", "")).strip()
        if not re.fullmatch(r"-?\d{3,20}|@[A-Za-z0-9_]{4,64}", chat):
            raise ValueError("chat_id de Telegram: número (o @canal)")
        return {"chat_id": chat}
    if not (settings.smtp_host and settings.smtp_from):
        raise ValueError("el operador no ha configurado el correo saliente (JH_SMTP_HOST / JH_SMTP_FROM)")
    return {}                                          # el destinatario es siempre el email de la cuenta


def _safe_user_url(url: str) -> None:
    if not url:
        raise ValueError("falta la URL")
    try:
        assert_public_url(url)
    except UnsafeURL as e:
        raise ValueError(f"URL no permitida: {e}")


def needs_secret(kind: str) -> bool:
    return kind in ("webhook", "telegram")            # webhook: clave HMAC; telegram: token del bot. ntfy: token opcional


# Indirección para tests: la única función que hace I/O HTTP.
def http_post(url: str, content: bytes, headers: dict, timeout: float) -> tuple[int, str]:
    r = httpx.post(url, content=content, headers=headers, timeout=timeout, follow_redirects=False)
    return r.status_code, r.text[:200]


def smtp_send(msg: EmailMessage) -> None:
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.notify_timeout_s) as c:
        if settings.smtp_starttls:
            c.starttls()
        if settings.smtp_user:
            c.login(settings.smtp_user, settings.smtp_password)
        c.send_message(msg)


def _jobs_lines(jobs: list[dict], limit: int = 10) -> list[str]:
    return [f"- {j.get('title', '?')} - {j.get('company', '')}" + (f" ({j['score']:.1f}/5)" if j.get("score") else "") + (f"\n  {j['url']}" if j.get("url") else "")
            for j in jobs[:limit]]


def compose(notification: dict, jobs: list[dict]) -> str:
    """Texto plano del aviso (ntfy, Telegram, email)."""
    lines = [notification["body"]] if notification.get("body") else []
    lines += _jobs_lines(jobs)
    if len(jobs) > 10:
        lines.append(f"... y {len(jobs) - 10} más en tu panel")
    return "\n".join(lines)


def send(kind: str, config: dict, secret: str | None, notification: dict, jobs: list[dict], delivery_id: int, user_email: str | None = None) -> dict:
    """Entrega un aviso. Devuelve {code, detail}; lanza DeliveryError si falla."""
    text = compose(notification, jobs)
    try:
        if kind == "webhook":
            return _webhook(config, secret, notification, jobs, delivery_id)
        if kind == "ntfy":
            server, topic = config["server"], config["topic"]
            check_url(server)
            headers = {"Title": notification["title"].encode("utf-8", "replace").decode("latin-1", "replace"), "Tags": "briefcase"}
            if secret:
                headers["Authorization"] = f"Bearer {secret}"
            code, detail = http_post(f"{server}/{quote(topic)}", text.encode(), headers, settings.notify_timeout_s)
        elif kind == "telegram":
            if not secret:
                raise DeliveryError("falta el token del bot", retry=False)
            url = f"{settings.notify_telegram_api.rstrip('/')}/bot{secret}/sendMessage"
            body = json.dumps({"chat_id": config["chat_id"], "text": f"{notification['title']}\n\n{text}"[:4000], "disable_web_page_preview": True}).encode()
            code, detail = http_post(url, body, {"Content-Type": "application/json"}, settings.notify_timeout_s)
        elif kind == "email":
            if not (settings.smtp_host and settings.smtp_from and user_email):
                raise DeliveryError("correo saliente no configurado", retry=False)
            msg = EmailMessage()
            msg["Subject"], msg["From"], msg["To"] = notification["title"], settings.smtp_from, user_email
            msg.set_content(text)
            smtp_send(msg)
            return {"code": None, "detail": "enviado por SMTP"}
        else:
            raise DeliveryError(f"canal desconocido: {kind}", retry=False)
    except DeliveryError:
        raise
    except Exception as e:  # noqa: BLE001
        raise DeliveryError(_scrub(f"{type(e).__name__}: {e}", secret))
    return _check_response(code, detail, secret)


def _check_response(code: int, detail: str, secret: str | None) -> dict:
    if 200 <= code < 300:
        return {"code": code, "detail": "ok"}
    if code == 429 or code >= 500:
        raise DeliveryError(f"HTTP {code}: {_scrub(detail, secret)}", retry=True, code=code)
    raise DeliveryError(f"HTTP {code}: {_scrub(detail, secret)}", retry=False, code=code)      # 4xx: reintentar no lo arregla


def _webhook(config: dict, secret: str | None, notification: dict, jobs: list[dict], delivery_id: int) -> dict:
    check_url(config["url"])
    body = json.dumps({"id": notification["id"], "kind": notification["kind"], "title": notification["title"], "body": notification["body"],
                       "created_at": notification["created_at"], "jobs": jobs, "data": notification.get("data") or {}}, ensure_ascii=False).encode()
    ts = str(int(time.time()))
    headers = {"Content-Type": "application/json", "X-JobHunter-Timestamp": ts, "X-JobHunter-Delivery": str(delivery_id), "User-Agent": "JobHunter-Webhook/1"}
    if secret:
        headers["X-JobHunter-Signature"] = sign(secret, ts, body)
    code, detail = http_post(config["url"], body, headers, settings.notify_timeout_s)
    return _check_response(code, detail, secret)


def host_of(config: dict) -> str:
    u = config.get("url") or config.get("server") or ""
    return urlsplit(u).hostname or ""
