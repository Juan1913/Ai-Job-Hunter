"""Tablas propias de la capa de autenticación (refresh tokens rotatorios)."""
from datetime import datetime

from sqlmodel import Field, SQLModel

from app.db import utcnow


class RefreshToken(SQLModel, table=True):
    """Solo se guarda el hash SHA-256 del token. `family` agrupa la cadena de rotaciones:
    si se reutiliza un token ya rotado se revoca toda la familia (robo detectado)."""
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    family: str = Field(index=True)
    token_hash: str = Field(unique=True, index=True)
    expires_at: datetime
    used_at: datetime | None = None
    revoked: bool = False
    created_at: datetime = Field(default_factory=utcnow)
