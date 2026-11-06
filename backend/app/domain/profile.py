from enum import Enum

from pydantic import BaseModel, Field


class SkillSource(str, Enum):
    CV = "cv"                      # extraída del CV subido
    USER_CONFIRMED = "confirmed"   # el usuario confirmó tenerla (p. ej. al adaptar un CV)


class Skill(BaseModel):
    name: str
    source: SkillSource = SkillSource.CV


class Experience(BaseModel):
    company: str
    title: str
    start: str | None = None
    end: str | None = None
    bullets: list[str] = Field(default_factory=list)


class Education(BaseModel):
    institution: str
    degree: str
    year: str | None = None


class DealBreakers(BaseModel):
    """Vetos duros del usuario para la evaluación (app/evaluation): cualquiera que se cumpla fuerza `skip` con la razón explícita.
    Todo es opcional; sin configurar no veta nada. Los umbrales de recomendación también son del usuario."""

    min_salary: float | None = None                 # mínimo aceptable; se compara con el máximo que publica la oferta
    salary_currency: str = "USD"                    # ISO-4217 del mínimo
    salary_period: str = "month"                    # hour | day | week | month | year
    excluded_companies: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)      # palabras que, si aparecen en título/descripción, descartan la oferta
    excluded_modes: list[str] = Field(default_factory=list)         # remote | hybrid | onsite
    min_level: str | None = None                    # intern | junior | mid | senior | lead | director (nivel que exige la oferta)
    max_level: str | None = None
    required_languages: list[str] = Field(default_factory=list)     # ISO-639: la oferta debe estar escrita en alguno de ellos
    apply_threshold: float = Field(3.8, ge=1, le=5)                 # puntaje >= esto (y sin vetos) => apply
    consider_threshold: float = Field(3.0, ge=1, le=5)              # puntaje >= esto => consider; por debajo => skip


class SearchPreferences(BaseModel):
    """Lo que el usuario elige en el panel: dónde y en qué idioma buscar."""

    countries: list[str] = Field(default_factory=list)   # ISO-3166, p. ej. ["CO", "MX"]
    regions: list[str] = Field(default_factory=list)     # ciudades/estados
    languages: list[str] = Field(default_factory=list)   # ISO-639, p. ej. ["es", "en"]
    remote: bool = True
    target_roles: list[str] = Field(default_factory=list)
    deal_breakers: DealBreakers | None = None      # vetos y umbrales de la evaluación (opcional, retrocompatible)


class Profile(BaseModel):
    """Perfil canónico: única fuente de verdad que usa el CV Writer."""

    full_name: str = ""
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    headline: str = ""
    summary: str = ""
    skills: list[Skill] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    preferences: SearchPreferences = Field(default_factory=SearchPreferences)

    def skill_names(self) -> set[str]:
        return {s.name.lower() for s in self.skills}
