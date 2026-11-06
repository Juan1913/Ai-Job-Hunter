import hashlib
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlmodel import Session, col, select

from app.auth_models import RefreshToken
from app.config import settings
from app.db import utcnow

_ph = PasswordHasher()
# Hash de relleno: verificar contra él cuando el usuario no existe iguala los tiempos de respuesta.
_DUMMY_HASH = _ph.hash("contraseña-de-relleno-para-igualar-tiempos")


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except (VerifyMismatchError, InvalidHashError):
        return False


def burn_password_check(pw: str) -> None:
    """Gasta el mismo tiempo que una verificación real (usuario inexistente)."""
    verify_password(pw, _DUMMY_HASH)


# ------------------------------------------------------------------ access token (JWT corto)
def password_version(password_hash: str) -> str:
    """Cambia al cambiar la contraseña: invalida de inmediato los access tokens anteriores."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:12]


def create_token(user_id: int, password_hash: str | None = None) -> str:
    now = utcnow()
    claims = {"sub": str(user_id), "typ": "access", "iat": now, "exp": now + timedelta(minutes=settings.jwt_ttl_minutes)}
    if password_hash:
        claims["pv"] = password_version(password_hash)
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def decode_claims(token: str) -> dict | None:
    try:
        c = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
        return c if c.get("typ") == "access" else None
    except jwt.PyJWTError:
        return None


def decode_token(token: str) -> int | None:
    c = decode_claims(token)
    try:
        return int(c["sub"]) if c else None
    except (KeyError, ValueError):
        return None


# ------------------------------------------------------------------ refresh token rotatorio
def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def issue_refresh(session: Session, user_id: int, family: str | None = None) -> str:
    token = secrets.token_urlsafe(48)
    session.add(RefreshToken(user_id=user_id, family=family or uuid.uuid4().hex, token_hash=_h(token),
                             expires_at=utcnow() + timedelta(days=settings.refresh_ttl_days)))
    session.commit()
    return token


def rotate_refresh(session: Session, token: str) -> tuple[int, str] | None:
    """Canjea un refresh token por otro nuevo. Devuelve (user_id, nuevo_token) o None si no vale.
    Reutilizar un token ya rotado revoca toda su familia."""
    row = session.exec(select(RefreshToken).where(RefreshToken.token_hash == _h(token))).first()
    if not row:
        return None
    if row.revoked or _aware(row.expires_at) < utcnow():
        return None
    if row.used_at is not None:
        revoke_family(session, row.family)
        return None
    row.used_at = utcnow()
    session.add(row)
    session.commit()
    return row.user_id, issue_refresh(session, row.user_id, row.family)


def revoke_family(session: Session, family: str) -> None:
    for r in session.exec(select(RefreshToken).where(RefreshToken.family == family)).all():
        r.revoked = True
        session.add(r)
    session.commit()


def revoke_refresh(session: Session, token: str) -> None:
    row = session.exec(select(RefreshToken).where(RefreshToken.token_hash == _h(token))).first()
    if row:
        revoke_family(session, row.family)


def revoke_all_refresh(session: Session, user_id: int) -> None:
    for r in session.exec(select(RefreshToken).where(RefreshToken.user_id == user_id, col(RefreshToken.revoked) == False)).all():  # noqa: E712
        r.revoked = True
        session.add(r)
    session.commit()


# ------------------------------------------------------------------ bloqueo de login
class LoginGuard:
    """Bloqueo temporal tras fallos repetidos. Cuenta por (ip, email) y, con umbral 5x, solo por email
    (así un atacante distribuido no puede probar claves sin límite, pero tampoco bloquea a la víctima
    con unos pocos intentos desde una sola IP). Cuenta igual si el email existe o no: no filtra nada."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._fails: dict[tuple, list[float]] = {}
        self._locked: dict[tuple, float] = {}

    def _keys(self, ip: str, email: str) -> list[tuple[tuple, int]]:
        n = settings.login_max_failures
        return [((ip, email), n), ((email,), n * 5)]

    def retry_after(self, ip: str, email: str) -> int:
        """Segundos restantes de bloqueo (0 = no bloqueado)."""
        now = self._clock()
        with self._lock:
            rem = 0.0
            for key, _ in self._keys(ip, email):
                until = self._locked.get(key)
                if until is not None:
                    if until <= now:
                        del self._locked[key]
                    else:
                        rem = max(rem, until - now)
            return int(rem) + 1 if rem > 0 else 0

    def failure(self, ip: str, email: str) -> None:
        now = self._clock()
        with self._lock:
            for key, limit in self._keys(ip, email):
                hist = [t for t in self._fails.get(key, []) if now - t < settings.login_fail_window_s] + [now]
                self._fails[key] = hist
                if len(hist) >= limit:
                    self._locked[key] = now + settings.login_lock_s
                    self._fails.pop(key, None)
            if len(self._fails) > 50_000:       # tope de memoria
                self._fails.clear()

    def success(self, ip: str, email: str) -> None:
        with self._lock:
            self._fails.pop((ip, email), None)
            self._fails.pop((email,), None)

    def reset(self) -> None:
        with self._lock:
            self._fails.clear()
            self._locked.clear()


login_guard = LoginGuard()
