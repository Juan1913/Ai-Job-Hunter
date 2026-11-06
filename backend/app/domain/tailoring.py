from enum import Enum

from pydantic import BaseModel, Field


class GapStatus(str, Enum):
    HAVE = "have"          # ya está en el perfil: se destaca
    ADJACENT = "adjacent"  # hay experiencia real que se reescribe con el vocabulario de la oferta
    MISSING = "missing"    # no está: solo entra al CV si el usuario lo confirma


class KeywordGap(BaseModel):
    keyword: str
    status: GapStatus
    evidence: str | None = None   # fragmento del perfil que respalda HAVE/ADJACENT
    confirmed: bool = False       # solo relevante para MISSING


class RuleResult(BaseModel):
    """Una regla del verificador ATS: cuántos puntos aportó y por qué."""
    rule: str
    ok: bool
    points: float
    max_points: float
    detail: str = ""

class AtsReport(BaseModel):
    score: int = Field(ge=0, le=100)
    keyword_coverage: float = Field(ge=0, le=1)
    issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)           # datos del perfil a completar (el redactor no puede arreglarlos)
    breakdown: list[RuleResult] = Field(default_factory=list)   # explicación del score por regla
    lang: str | None = None
    artifact_score: int | None = None                           # puntaje sobre la capa de texto del PDF real (lo que ve el escáner)


def includable(gap: KeywordGap) -> bool:
    """Regla de oro: nada que el usuario no tenga o no haya confirmado entra al CV."""
    return gap.status != GapStatus.MISSING or gap.confirmed


class TailoredCV(BaseModel):
    headline: str
    summary: str
    skills: list[str]
    experience: list[dict]            # {company, title, start, end, bullets}
    education: list[dict] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    contact: dict = Field(default_factory=dict)
    text: str = ""                    # versión texto plano que lee el ATS
    lang: str | None = None           # idioma del CV (el de la oferta): 'es' | 'en'
    ats: AtsReport | None = None
    trace: list[str] = Field(default_factory=list)
    # --- auditoría de fidelidad y verificación del archivo (aditivos y opcionales: los CVs guardados antes siguen siendo válidos)
    audit: dict | None = None          # AuditReport.compact(): veredicto + líneas marcadas con su origen en el perfil
    review_required: bool = False      # quedó una afirmación 'unsupported' de severidad alta: el usuario debe revisarla
    artifact: dict | None = None       # ArtifactReport: puntaje del PDF extraído vs texto lógico, palabras perdidas, plantilla
    fit: dict | None = None            # FitResult del último ajuste a páginas
