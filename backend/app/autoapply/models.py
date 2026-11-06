"""Datos y política de la auto-postulación. Todo es del usuario y por defecto está APAGADO."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Mode = Literal["off", "approval", "auto"]
CvMode = Literal["base", "tailored"]


class QA(BaseModel):
    """Respuesta fija a una pregunta frecuente de formularios: si la etiqueta del campo coincide con `pattern` (regex, sin distinguir mayúsculas), se responde `answer`."""
    pattern: str = Field(max_length=200)
    answer: str = Field(max_length=2000)


class ApplyData(BaseModel):
    """Lo que se escribe en los formularios. El nombre, el correo y el teléfono base salen del perfil; aquí se completan o se sobreescriben."""
    full_name: str | None = Field(None, max_length=200)
    email: str | None = Field(None, max_length=254)
    phone: str | None = Field(None, max_length=60)
    city: str | None = Field(None, max_length=120)
    linkedin_url: str | None = Field(None, max_length=300)
    github_url: str | None = Field(None, max_length=300)
    portfolio_url: str | None = Field(None, max_length=300)
    expected_salary: str | None = Field(None, max_length=80)
    notice_period: str | None = Field(None, max_length=80)
    answers: list[QA] = Field(default_factory=list, max_length=40)
    accept_privacy_notices: bool = False      # marcar casillas de «acepto la política de privacidad / tratamiento de datos» (nunca las de marketing)
    decline_demographics: bool = True         # en preguntas voluntarias de género/etnia, elegir «prefiero no decir»


class AutoApplyPolicy(BaseModel):
    mode: Mode = "off"                        # off: no hace nada · approval: prepara y TÚ apruebas con un clic · auto: envía sola
    paused: bool = False                      # interruptor de emergencia
    dry_run: bool = True                      # ensayo: rellena el formulario pero NO lo envía (se desactiva a propósito)
    min_score: float = Field(3.8, ge=1, le=5)
    only_recommended: bool = True             # solo ofertas «vale la pena postular»
    daily_cap: int = Field(5, ge=1, le=30)
    cv_mode: CvMode = "tailored"              # base: tu CV general · tailored: un CV hecho para cada puesto
    cover_letter: bool = False
    portal_modes: dict[str, Mode] = Field(default_factory=dict)   # por portal/fuente (p. ej. «lever»): sobrescribe `mode`
