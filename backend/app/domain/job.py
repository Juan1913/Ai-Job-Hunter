import hashlib
import re

from pydantic import BaseModel, Field, computed_field, field_validator

from app.text import norm

_LEGAL = re.compile(r"\b(s ?a ?s|s ?a|ltda|inc|llc|ltd|gmbh|corp|corporation|s ?l|s ?r ?l|de colombia|colombia)\b")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", norm(s)).strip()


def _company(s: str) -> str:
    return re.sub(r"\s+", " ", _LEGAL.sub(" ", _slug(s))).strip()


def _city(loc: str) -> str:
    """Solo la ciudad: 'Bogotá, Cundinamarca' y 'Bogota, D.C., Capital District, Colombia' son la misma oferta."""
    city = _slug(loc.split(",")[0]) if loc else ""
    return re.sub(r"\b(d c|distrito capital|capital district|dc)\b", " ", city).strip()


class Job(BaseModel):
    title: str
    company: str = ""
    location: str = ""
    remote: bool = False
    description: str = ""
    url: str
    source: str                      # "computrabajo", "greenhouse:mercadolibre", ...
    posted_at: str | None = None
    salary: str | None = None
    apply_url: str | None = None     # destino real si es externo
    # --- campos normalizados (todos opcionales: las ofertas guardadas antes siguen siendo válidas) ---
    country: str | None = None       # ISO-3166 alfa-2 ("CO")
    language: str | None = None      # ISO-639 detectado en la descripción ("es", "en")
    work_mode: str | None = None     # remote | hybrid | onsite
    employment_type: str | None = None   # full_time | part_time | contract | internship | temporary
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None   # ISO-4217
    salary_period: str | None = None     # hour | day | week | month | year
    closed: bool = False             # la oferta ya no acepta postulaciones
    expires_at: str | None = None    # ISO date, si la fuente lo informa
    # --- calidad (todo aditivo): salario anualizado comparable y descripción en markdown ---
    salary_annual_min: float | None = None    # salary_min llevado a año (misma moneda; NO se convierte entre monedas)
    salary_annual_max: float | None = None
    salary_period_inferred: bool = False      # el periodo no venía en la oferta: se dedujo (ver normalize.annualize)
    description_md: str | None = None         # descripción en markdown (listas/encabezados), con tope de tamaño
    # --- portón de integridad (app/safety): hallazgos de severidad media/alta en el texto de la oferta ---
    integrity_flags: list[str] = Field(default_factory=list)   # p. ej. ["hidden_html", "instruction_injection"]
    integrity_risk: str | None = None                           # medium | high (None = sin hallazgos relevantes)
    raw: dict = Field(default_factory=dict, exclude=True)

    @field_validator("posted_at", "expires_at", mode="before")
    @classmethod
    def _date_as_str(cls, v):
        """Algunas fuentes entregan epoch numérico: se guarda como texto y finalize() lo normaliza a ISO."""
        return None if v is None else str(v)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def dedup_key(self) -> str:
        """Misma oferta publicada en varios portales -> misma clave."""
        key = "|".join((_slug(self.title), _company(self.company), _city(self.location)))
        return hashlib.sha1(key.encode()).hexdigest()[:16]


class SearchQuery(BaseModel):
    keywords: list[str]
    countries: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    remote: bool = True
    limit_per_source: int = 25
    # --- filtros y control (todo aditivo; los valores por defecto dejan el comportamiento anterior) ---
    variants: list[str] = Field(default_factory=list)       # sinónimos/traducciones de `keywords` (app/scout/query.py)
    expand: bool = True                                    # el servicio expande la consulta si no vienen variants
    request_budget: int = 6                                # consultas distintas máx. por fuente (variantes) y por país
    hours_old: int | None = None                           # antigüedad máxima en horas (como JobSpy). Granularidad real: días
    employment_types: list[str] = Field(default_factory=list)   # full_time | part_time | contract | internship | temporary
    work_modes: list[str] = Field(default_factory=list)         # remote | hybrid | onsite
    strict_filters: bool = False                           # True: descarta también las ofertas con dato desconocido
    offset: int = 0                                        # ofertas a saltar por fuente (paginación)
    fetch_description: bool = True                         # False: no abrir el detalle de cada oferta (menos peticiones)
