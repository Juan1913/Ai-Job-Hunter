"""Cuenta del usuario: datos propios, cambio de clave/email, exportación y borrado total (RGPD)."""
import base64
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Table, delete, or_
from sqlalchemy import select as sa_select
from sqlmodel import Session, SQLModel, select

from app.api.auth import EMAIL, TokenOut, check_new_password, issue_tokens
from app.api.deps import current_user
from app.db import User, get_session
from app.security import hash_password, revoke_all_refresh, verify_password

log = logging.getLogger(__name__)
router = APIRouter(prefix="/me", tags=["account"])

# Columnas que nunca salen en la exportación (secretos, no datos del usuario).
_SECRET_COLS = {"password_hash", "token_hash", "secret_enc"}
_SKIP_EXPORT = {"refreshtoken"}


def _tables() -> tuple[list[Table], set[Table]]:
    tabs = list(SQLModel.metadata.sorted_tables)           # padres antes que hijos
    owned = {t for t in tabs if "user_id" in t.c and t.name != "user"}
    return tabs, owned


def _owner_condition(table: Table, uid: int, owned: set[Table]):
    """Condición SQL «esta fila es del usuario». Es genérica: cubre cualquier tabla que otros
    módulos añadan, ya tenga `user_id` o cuelgue (FK) de una tabla del usuario (p. ej. job_id)."""
    conds = []
    if "user_id" in table.c:
        conds.append(table.c.user_id == uid)
    for fk in table.foreign_keys:
        target = fk.column.table
        if target.name == "user" and fk.parent.name != "user_id":
            conds.append(fk.parent == uid)
        elif target in owned and target is not table:
            conds.append(fk.parent.in_(sa_select(fk.column).where(_owner_condition(target, uid, owned))))
    return or_(*conds) if conds else None


def delete_user_data(session: Session, uid: int) -> dict[str, int]:
    tabs, owned = _tables()
    counts: dict[str, int] = {}
    for t in reversed(tabs):                               # hijos antes que padres (FK)
        if t.name == "user":
            continue
        cond = _owner_condition(t, uid, owned)
        if cond is not None:
            n = session.execute(delete(t).where(cond)).rowcount
            if n:
                counts[t.name] = n
    session.execute(delete(User).where(User.id == uid))
    session.commit()
    return counts


def export_user_data(session: Session, user: User) -> dict:
    tabs, owned = _tables()
    data: dict[str, list] = {}
    for t in tabs:
        if t.name in ("user", *_SKIP_EXPORT):
            continue
        cond = _owner_condition(t, user.id, owned)
        if cond is None:
            continue
        rows = session.execute(sa_select(t).where(cond)).mappings().all()
        data[t.name] = [{k: v for k, v in r.items() if k not in _SECRET_COLS} for r in rows]
    return jsonable_encoder(custom_encoder={bytes: lambda b: base64.b64encode(b).decode()}, obj={
        "exported_at": datetime.now(timezone.utc),
        "user": {"id": user.id, "email": user.email, "created_at": user.created_at},
        "data": data,
    })


class AccountOut(BaseModel):
    id: int
    email: str
    created_at: str


@router.get("", response_model=AccountOut)
def me(user: User = Depends(current_user)):
    return AccountOut(id=user.id, email=user.email, created_at=user.created_at.isoformat())


@router.get("/export")
def export(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Portabilidad de datos: todo lo que el sistema guarda de ti, en JSON."""
    return JSONResponse(export_user_data(session, user),
                        headers={"Content-Disposition": f'attachment; filename="jobhunter-export-{user.id}.json"'})


@router.delete("", status_code=204)
def delete_account(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Borra la cuenta y TODOS sus datos (perfil, ofertas, CVs, búsquedas, sesiones...). Irreversible."""
    uid = user.id
    counts = delete_user_data(session, uid)
    log.info("cuenta eliminada", extra={"deleted_user": uid, "rows": counts})


class PasswordIn(BaseModel):
    current_password: str
    new_password: str


@router.post("/password", response_model=TokenOut)
def change_password(body: PasswordIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Cambia la clave, cierra todas las demás sesiones y devuelve tokens nuevos para esta."""
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(403, "La contraseña actual no es correcta")
    check_new_password(body.new_password)
    user.password_hash = hash_password(body.new_password)
    session.add(user)
    session.commit()
    revoke_all_refresh(session, user.id)
    return issue_tokens(session, user)


class EmailIn(BaseModel):
    email: str
    password: str


@router.put("/email", response_model=AccountOut)
def change_email(body: EmailIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    email = body.email.strip().lower()
    if len(email) > 254 or not EMAIL.match(email):
        raise HTTPException(422, "email inválido")
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(403, "La contraseña no es correcta")
    if email != user.email and session.exec(select(User).where(User.email == email)).first():
        raise HTTPException(409, "No se pudo cambiar el email")
    user.email = email
    session.add(user)
    session.commit()
    return AccountOut(id=user.id, email=user.email, created_at=user.created_at.isoformat())
