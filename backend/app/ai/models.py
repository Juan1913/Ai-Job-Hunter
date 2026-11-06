"""Tablas de la capa de IA: contabilidad de uso (presupuesto en modo api) y consentimiento del usuario."""
from datetime import date, datetime, timezone

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LLMUsage(SQLModel, table=True):
    """Uso diario por (usuario, modelo). `user_id` 0 = tareas del sistema (sin usuario). Nunca guarda texto ni claves."""
    __table_args__ = (UniqueConstraint("user_id", "day", "model"),)
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(default=0, index=True)
    day: date = Field(index=True)                     # día UTC
    model: str = ""
    provider: str = ""                                # openai | anthropic | ollama
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0                             # estimado con la tarifa configurada (0 si no hay tarifa)
    updated_at: datetime = Field(default_factory=_now)


class AiConsent(SQLModel, table=True):
    """Consentimiento explícito para que los textos de CV/ofertas salgan a un proveedor externo (modo api)."""
    user_id: int = Field(primary_key=True, foreign_key="user.id")
    accepted: bool = False
    version: str = ""                                 # versión del aviso de privacidad aceptada
    updated_at: datetime = Field(default_factory=_now)
