import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlmodel import Session, select

from app.config import settings
from app.db import User, get_session
from app.observability import metrics
from app.security import (
    burn_password_check,
    create_token,
    hash_password,
    issue_refresh,
    login_guard,
    revoke_refresh,
    rotate_refresh,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_PASSWORD = 256     # argon2 con claves enormes es un vector de DoS


class Credentials(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if len(v) > 254 or not EMAIL.match(v):
            raise ValueError("email inválido")
        return v


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    refresh_token: str | None = None
    expires_in: int = 0


class RefreshIn(BaseModel):
    refresh_token: str


def issue_tokens(session: Session, user: User) -> TokenOut:
    return TokenOut(access_token=create_token(user.id, user.password_hash), refresh_token=issue_refresh(session, user.id),
                    expires_in=settings.jwt_ttl_minutes * 60)


def check_new_password(pw: str) -> None:
    if len(pw) < 10:
        raise HTTPException(422, "La contraseña debe tener al menos 10 caracteres")
    if len(pw) > MAX_PASSWORD:
        raise HTTPException(422, f"La contraseña no puede superar {MAX_PASSWORD} caracteres")


def _ip(request: Request) -> str:
    return getattr(request.state, "client_ip", None) or (request.client.host if request.client else "unknown")


@router.post("/register", response_model=TokenOut, status_code=201)
def register(c: Credentials, request: Request, session: Session = Depends(get_session)):
    check_new_password(c.password)
    if session.exec(select(User).where(User.email == c.email)).first():
        # Sin verificación de email no se puede ocultar del todo que la cuenta existe; al menos
        # el mensaje es neutro, tarda lo mismo que un alta real y /auth/* tiene rate limit estricto.
        burn_password_check(c.password)
        raise HTTPException(409, "No se pudo crear la cuenta. Si ya tienes una, inicia sesión")
    user = User(email=c.email, password_hash=hash_password(c.password))
    session.add(user)
    session.commit()
    session.refresh(user)
    return issue_tokens(session, user)


@router.post("/login", response_model=TokenOut)
def login(c: Credentials, request: Request, session: Session = Depends(get_session)):
    ip = _ip(request)
    wait = login_guard.retry_after(ip, c.email)
    if wait:
        metrics.inc("jh_rate_limited_total", scope="login_lock")
        raise HTTPException(429, "Demasiados intentos fallidos. Inténtalo más tarde", headers={"Retry-After": str(wait)})
    user = session.exec(select(User).where(User.email == c.email)).first()
    if user:
        ok = verify_password(c.password, user.password_hash)
    else:
        burn_password_check(c.password)
        ok = False
    if not ok:
        login_guard.failure(ip, c.email)
        metrics.inc("jh_login_failures_total")
        raise HTTPException(401, "Credenciales inválidas")
    login_guard.success(ip, c.email)
    return issue_tokens(session, user)


@router.post("/refresh", response_model=TokenOut)
def refresh(body: RefreshIn, session: Session = Depends(get_session)):
    """Rotación: cada refresh token sirve una sola vez; reutilizar uno viejo revoca la sesión entera."""
    res = rotate_refresh(session, body.refresh_token)
    user = session.get(User, res[0]) if res else None
    if not res or not user:
        raise HTTPException(401, "Refresh token inválido o caducado")
    return TokenOut(access_token=create_token(user.id, user.password_hash), refresh_token=res[1],
                    expires_in=settings.jwt_ttl_minutes * 60)


@router.post("/logout", status_code=204)
def logout(body: RefreshIn, session: Session = Depends(get_session)):
    revoke_refresh(session, body.refresh_token)
