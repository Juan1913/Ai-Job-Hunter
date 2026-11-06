"""Consentimiento explícito para enviar datos a un proveedor externo (modo api)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session

from app.ai.models import AiConsent
from app.config import settings
from app.db import engine


def has_consent(user_id: int) -> bool:
    with Session(engine) as s:
        row = s.get(AiConsent, user_id)
        return bool(row and row.accepted and row.version == settings.ai_privacy_version)


def set_consent(session: Session, user_id: int, accepted: bool) -> AiConsent:
    row = session.get(AiConsent, user_id) or AiConsent(user_id=user_id)
    row.accepted, row.version, row.updated_at = accepted, settings.ai_privacy_version, datetime.now(timezone.utc)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
