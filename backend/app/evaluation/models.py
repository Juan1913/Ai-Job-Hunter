"""Estructura ESTABLE del informe de evaluación (base del panel). Cambios incompatibles => subir `schema_version`."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1

Recommendation = Literal["apply", "consider", "skip"]
DimStatus = Literal["ok", "warning", "neutral", "veto"]

# Pesos por defecto (suman 1.0). Una dimensión sin dato (neutral) no pesa: su peso se reparte entre las demás.
WEIGHTS: dict[str, float] = {"skills": 0.35, "seniority": 0.20, "location": 0.15, "language": 0.10, "salary": 0.10, "freshness": 0.10}


class Dimension(BaseModel):
    key: Literal["skills", "seniority", "location", "language", "salary", "freshness"]
    label: str
    score: float = Field(ge=0, le=1, description="0 = mal ajuste, 1 = ajuste perfecto; 0.5 cuando no hay dato (status=neutral)")
    weight: float = Field(description="peso nominal de la dimensión")
    effective_weight: float = Field(description="peso realmente aplicado tras repartir el de las dimensiones sin dato (0 si neutral)")
    status: DimStatus = "ok"
    explanation: str                                    # frase corta en el idioma del usuario, con la evidencia
    evidence: list[str] = Field(default_factory=list)   # datos concretos usados (citas, cifras)
    source: str = ""                                    # de dónde sale el dato: 'análisis LLM cacheado', 'etapa 1 sin LLM', 'regex sobre la descripción'...


class Veto(BaseModel):
    code: str           # expired | closed | excluded_company | excluded_keyword | excluded_mode | salary_below_min | level_out_of_range | language_required | language_missing
    reason: str         # explicación para el usuario
    evidence: str = ""


class Flag(BaseModel):
    code: str           # urgent | overqualified | underqualified | integrity | no_salary | no_analysis | salary_unknown_currency | ...
    message: str
    severity: Literal["info", "warning", "alert"] = "info"


class Requirements(BaseModel):
    """Requisitos extraídos de la oferta (regex; opcionalmente refinados por el LLM y cacheados aquí)."""
    years: float | None = None
    level: str | None = None            # intern | junior | mid | senior | lead | director
    languages: list[dict] = Field(default_factory=list)   # [{code, level (0-6|None), required (bool)}]
    source: str = "regex"               # regex | llm
    fingerprint: str = ""               # hash del texto del que salió (para invalidar el caché)


class Evaluation(BaseModel):
    schema_version: int = SCHEMA_VERSION
    job_id: int | None = None
    lang: str = "es"
    score: float = Field(ge=1, le=5, description="puntaje global 1.0-5.0 (con vetos se limita a 2.0)")
    raw_score: float = Field(ge=1, le=5, description="puntaje antes de aplicar vetos")
    recommendation: Recommendation
    thresholds: dict[str, float]        # {apply, consider} vigentes para este usuario
    dimensions: list[Dimension]
    vetoes: list[Veto] = Field(default_factory=list)
    flags: list[Flag] = Field(default_factory=list)
    reasons_top3: list[str] = Field(default_factory=list)
    weights_explained: str = ""         # cómo se combinó el puntaje, legible
    requirements: Requirements | None = None
    generated_at: str
    fingerprint: str = ""               # hash de las entradas; igual => el informe guardado sigue vigente
    cached: bool = False
    ai_used: bool = False               # la evaluación es determinista; True solo si el LLM refinó años/nivel/idiomas
    ai_reason: str | None = None        # por qué no se usó IA (mode_none | no_consent | budget_exhausted | not_needed ...)
