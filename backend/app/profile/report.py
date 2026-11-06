"""Informe de calidad de la extracción del perfil: confianza por campo para que el panel pida revisión."""
from pydantic import BaseModel, Field

from app.domain.profile import Profile

REVIEW_THRESHOLD = 0.6

class FieldQuality(BaseModel):
    confidence: float = Field(ge=0, le=1)
    source: str                              # regex | heuristic | llm | fallback | missing
    needs_review: bool = False
    notes: list[str] = Field(default_factory=list)

class ProfileReport(BaseModel):
    overall: float = 0.0
    fields: dict[str, FieldQuality] = Field(default_factory=dict)
    needs_review: list[str] = Field(default_factory=list)       # campos que el usuario debería revisar
    warnings: list[str] = Field(default_factory=list)
    dropped: list[str] = Field(default_factory=list)            # lo que el modelo dijo y NO aparece en el CV (descartado)
    sections_found: list[str] = Field(default_factory=list)
    entries: dict[str, list[dict]] = Field(default_factory=dict)   # calidad por entrada (experience / education)
    llm_used: bool = False
    llm_calls: int = 0
    llm_failures: int = 0
    extraction_method: str | None = None
    language: str | None = None

class BuildResult(BaseModel):
    profile: Profile
    report: ProfileReport

WEIGHTS = {"full_name": 2, "email": 2, "phone": 1, "headline": 1, "summary": 0.5, "skills": 2, "experience": 3, "education": 1.5,
           "languages": 0.3, "location": 0.3, "certifications": 0.2}

OPTIONAL = {"certifications", "languages", "location", "summary", "phone"}

def finalize(report: ProfileReport) -> ProfileReport:
    total = sum(WEIGHTS.get(k, 0.5) for k in report.fields)
    got = sum(WEIGHTS.get(k, 0.5) * q.confidence for k, q in report.fields.items())
    report.overall = round(got / total, 2) if total else 0.0
    for name, q in report.fields.items():
        optional_missing = q.source == "missing" and name in OPTIONAL
        q.needs_review = q.confidence < REVIEW_THRESHOLD and not optional_missing
    report.needs_review = [k for k, q in report.fields.items() if q.needs_review]
    return report
