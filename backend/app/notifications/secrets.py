"""Cifrado de los secretos de canal (token de Telegram/ntfy, clave HMAC del webhook) en reposo, solo con la biblioteca estándar.

Construcción: clave maestra = HMAC-SHA256(JH_SECRETS_KEY | JH_JWT_SECRET, "jh-channel-secrets"); de ella salen una clave de cifrado y otra de MAC.
Cifrado = XOR con un keystream HMAC-SHA256(k_enc, nonce || contador) (PRF en modo contador) y etiqueta HMAC-SHA256(k_mac, nonce || cifrado)
(encrypt-then-MAC, comparada en tiempo constante). El formato es `v1.<base64url(nonce|cifrado|etiqueta)>`.
Si se cambia la clave maestra, los secretos guardados dejan de descifrarse (el usuario los vuelve a introducir)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

from app.config import settings

PREFIX = "v1."


class SecretError(ValueError):
    pass


def _keys() -> tuple[bytes, bytes]:
    master = hmac.new((settings.secrets_key or settings.jwt_secret).encode(), b"jh-channel-secrets", hashlib.sha256).digest()
    return (hmac.new(master, b"enc", hashlib.sha256).digest(), hmac.new(master, b"mac", hashlib.sha256).digest())


def _stream(k: bytes, nonce: bytes, n: int) -> bytes:
    out, i = b"", 0
    while len(out) < n:
        out += hmac.new(k, nonce + i.to_bytes(8, "big"), hashlib.sha256).digest()
        i += 1
    return out[:n]


def encrypt(plain: str) -> str:
    k_enc, k_mac = _keys()
    data, nonce = plain.encode(), os.urandom(16)
    ct = bytes(a ^ b for a, b in zip(data, _stream(k_enc, nonce, len(data))))
    tag = hmac.new(k_mac, nonce + ct, hashlib.sha256).digest()
    return PREFIX + base64.urlsafe_b64encode(nonce + ct + tag).decode()


def decrypt(token: str) -> str:
    if not token.startswith(PREFIX):
        raise SecretError("formato de secreto desconocido")
    k_enc, k_mac = _keys()
    try:
        raw = base64.urlsafe_b64decode(token[len(PREFIX):].encode())
    except Exception as e:  # noqa: BLE001
        raise SecretError("secreto corrupto") from e
    if len(raw) < 48:
        raise SecretError("secreto corrupto")
    nonce, ct, tag = raw[:16], raw[16:-32], raw[-32:]
    if not hmac.compare_digest(tag, hmac.new(k_mac, nonce + ct, hashlib.sha256).digest()):
        raise SecretError("el secreto no se puede descifrar (¿cambió JH_SECRETS_KEY / JH_JWT_SECRET?)")
    return bytes(a ^ b for a, b in zip(ct, _stream(k_enc, nonce, len(ct)))).decode()
