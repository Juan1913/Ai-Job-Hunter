"""Estado de la IA (GET /ai/status) y consentimiento del usuario para enviar datos a un proveedor externo."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlmodel import Session

from app.ai import consent as consent_store
from app.ai import status as ai_status
from app.api.deps import bearer, current_user
from app.db import User, get_session

router = APIRouter(tags=["ai"])


def optional_user(cred: HTTPAuthorizationCredentials | None = Depends(bearer), session: Session = Depends(get_session)) -> User | None:
    try:
        return current_user(cred, session) if cred else None
    except HTTPException:
        return None


@router.get("/ai/status")
def status(user: User | None = Depends(optional_user)):
    """Sin autenticación: modo, si los datos salen a un tercero, proveedor (host y modelos, NUNCA la clave), capacidades por función
    y si el presupuesto global está agotado. Con sesión añade `you`: consentimiento, uso y presupuesto propios."""
    return ai_status.user_status(user.id) if user else ai_status.public_status()


class ConsentIn(BaseModel):
    accepted: bool


@router.put("/me/ai/consent")
def set_consent(body: ConsentIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Consentimiento explícito para el modo api: sin él, tus textos NO salen y se usa la ruta sin IA."""
    consent_store.set_consent(session, user.id, body.accepted)
    return ai_status.user_status(user.id)


@router.get("/me/ai")
def my_ai(user: User = Depends(current_user)):
    return ai_status.user_status(user.id)
