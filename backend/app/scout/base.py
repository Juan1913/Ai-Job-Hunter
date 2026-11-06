import re
import unicodedata
from typing import Protocol

from app.domain.geo import COUNTRIES, guess_country
from app.domain.job import Job, SearchQuery


class Source(Protocol):
    name: str
    countries: set[str]   # vacío = global

    def search(self, query: SearchQuery) -> list[Job]: ...


def supports(source: Source, query: SearchQuery) -> bool:
    """¿Esta fuente aplica a la búsqueda? Cubre el país pedido y, si es solo-remoto, el usuario acepta remoto."""
    if getattr(source, "remote_only", False) and not query.remote:
        return False
    return not source.countries or not query.countries or bool(source.countries & set(query.countries))


# ---------------------------------------------------------------- utilidades compartidas por las fuentes
def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower()) if not unicodedata.combining(c))


def match_keywords(text: str, keywords: list[str]) -> bool:
    """Para feeds que devuelven TODO: la oferta coincide si algún término (todas sus palabras) aparece en el texto."""
    if not any(k.strip() for k in keywords):
        return True
    hay = _fold(text)
    for phrase in keywords:
        words = [w for w in re.findall(r"[\w#+.]+", _fold(phrase)) if len(w) > 2 or w in {"c#", "c++", "ia", "ai", "qa", "ux", "ui"}]
        if words and all(w in hay for w in words):
            return True
    return False


_WORLDWIDE = re.compile(r"world\s*wide|anywhere|global|international|latam|latin america|americas|todo el mundo|cualquier|remote$|^remote\b|^$", re.I)


def geo_ok(location: str, query: SearchQuery) -> bool:
    """Ofertas remotas con restricción geográfica ('USA only'): se descartan si no cubren los países/regiones del usuario."""
    if not query.countries and not query.regions:
        return True
    loc = _fold(location or "").strip()
    if not loc or _WORLDWIDE.search(loc):
        return True
    needles = [_fold(r) for r in query.regions]
    for code in query.countries:
        c = COUNTRIES.get(code)
        needles += [_fold(c.name), code.lower(), *(_fold(a) for a in c.aliases)] if c else [code.lower()]
    return any(re.search(rf"(?<!\w){re.escape(n)}(?!\w)", loc) for n in needles if n)


def cap(query: SearchQuery) -> int:
    return max(1, query.limit_per_source)


_STOPWORDS = {"de", "la", "el", "en", "y", "para", "con", "los", "las", "del", "un", "una", "the", "and", "for", "with", "of", "to", "a", "in", "senior", "junior"}


def _terms(keywords: list[str]) -> set[str]:
    return {w for k in keywords for w in re.findall(r"[\w#+]+", _fold(k)) if len(w) >= 3 and w not in _STOPWORDS}


def all_terms(query: SearchQuery) -> list[str]:
    """Consulta original + variantes (sinónimos/traducciones) para las fuentes que filtran EN LOCAL."""
    return [*query.keywords, *query.variants]


def relevance(title: str, body: str, keywords: list[str], variants: list[str] | None = None) -> int:
    """Puntaje de coincidencia con las palabras clave: el título pesa más que la descripción.
    Las consultas originales puntúan con que case UNA palabra (como siempre); cada variante exige al menos la mitad de sus
    palabras, para que 'security analyst' no arrastre cualquier 'analyst'."""
    t, b = _fold(title), _fold(body[:2500])
    terms = _terms(keywords)
    if not terms and not variants:
        return 1
    best = 3 * sum(1 for w in terms if w in t) + sum(1 for w in terms if w in b)
    for v in variants or []:
        vt = _terms([v])
        need = max(1, (len(vt) + 1) // 2)
        in_t, in_b = sum(1 for w in vt if w in t), sum(1 for w in vt if w in b)
        if vt and max(in_t, in_b) >= need:
            best = max(best, 3 * in_t + in_b)
    return best


def location_ok(job: Job, query: SearchQuery) -> bool:
    """Para tableros de empresa: una oferta física debe estar en un país pedido; remoto o ubicación desconocida pasan."""
    if not query.countries or job.remote or job.work_mode == "remote" or not job.location.strip():
        return True
    code = job.country or guess_country(job.location)
    return code is None or code in query.countries or geo_ok(job.location, query)


def filter_company_jobs(jobs: list[Job], query: SearchQuery) -> list[Job]:
    """Un tablero de empresa devuelve TODAS sus vacantes: se deja lo relevante y del país, ordenado por relevancia."""
    scored = [(relevance(j.title, j.description, query.keywords, query.variants), j) for j in jobs if location_ok(j, query)]
    scored = [x for x in scored if x[0] > 0]
    scored.sort(key=lambda x: -x[0])
    return [j for _, j in scored[: cap(query)]]


# ---------------------------------------------------------------- qué filtros son NATIVOS y cuáles LOCALES, por fuente
# El filtro local (normalize.matches_filters) se aplica SIEMPRE en el servicio, también cuando la fuente filtró de forma nativa:
# así el resultado no depende de que la fuente respete el parámetro. "nativo" ahorra peticiones/ofertas inútiles.
LOCAL = "local"
NATIVE_FILTERS: dict[str, dict[str, str]] = {
    "indeed": {"query": "OR/comillas en una sola petición", "hours_old": "nativo: fromage 1|3|7|14 días (mayor -> solo local)", "offset": "nativo: start",
               "employment_types": LOCAL, "work_modes": LOCAL},
    "linkedin": {"query": "OR/comillas en una sola petición", "hours_old": "nativo: f_TPR en segundos (exacto)", "offset": "nativo: start",
                 "employment_types": "nativo: f_JT", "work_modes": "nativo: f_WT"},
    "adzuna": {"query": "varias consultas (tope)", "hours_old": "nativo: max_days_old", "offset": "nativo: página", "employment_types": "nativo: full_time/part_time/contract",
               "work_modes": LOCAL},
    "jooble": {"query": "una consulta (cupo gratuito de por vida)", "hours_old": "nativo: datecreatedfrom (+ local)", "offset": "nativo: página",
               "employment_types": LOCAL, "work_modes": LOCAL},
    "infojobs": {"query": "varias consultas (tope)", "hours_old": "nativo: sinceDate 24h/7d/15d (+ local)", "offset": "nativo: página", "employment_types": LOCAL, "work_modes": LOCAL},
}
DEFAULT_FILTERS = {"query": "varias consultas con tope (portales/APIs con búsqueda) o filtro local por cualquier variante (feeds y tableros de empresa)",
                   "hours_old": LOCAL, "offset": LOCAL + " (se pide offset+límite y se salta el inicio)", "employment_types": LOCAL, "work_modes": LOCAL}


def filter_support(source_name: str) -> dict[str, str]:
    """Cómo aplica cada filtro una fuente: 'nativo ...' o 'local'."""
    return {**DEFAULT_FILTERS, **NATIVE_FILTERS.get(source_name.split(":")[0], {})}
