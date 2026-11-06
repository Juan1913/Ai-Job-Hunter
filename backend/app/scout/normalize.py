"""Normalización común a todas las fuentes: texto limpio, fecha ISO, salario/moneda, remoto/híbrido,
idioma detectado, país y ofertas cerradas. `finalize()` se aplica una vez en el servicio de búsqueda,
así cada fuente solo extrae datos crudos y no repite esta lógica."""
import math
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

from app.domain.geo import COUNTRIES, LATAM, default_currency, guess_country
from app.domain.job import Job, SearchQuery
from app.safety.htmlstrip import strip_hidden_html
from app.safety.integrity import looks_like_instruction
from app.scout.markdown import html_to_markdown

# ---------------------------------------------------------------- texto
_BLOCK_TAGS = re.compile(r"</?(?:p|div|br|li|ul|ol|h[1-6]|tr|table)[^>]*>", re.I)
_TAGS = re.compile(r"<[^>]+>")


def clean_html(html: str | None) -> str:
    """HTML (incluso doblemente escapado, como Greenhouse) -> texto plano en una línea."""
    s = html or ""
    if "&lt;" in s and "<" not in s:
        s = unescape(s)
    s = strip_hidden_html(s, looks_like_instruction)      # texto oculto fuera ANTES de quitar las etiquetas (ver app/safety)
    s = _TAGS.sub(" ", _BLOCK_TAGS.sub(" ", s))
    return re.sub(r"\s+", " ", unescape(s)).strip()


# ---------------------------------------------------------------- idioma
_STOP = {
    "es": "de la el en y con para los las del un una por se que su al como más es son nuestro equipo experiencia trabajo años conocimientos "
          "buscamos requisitos ofrecemos funciones responsabilidades empresa oferta vacante".split(),
    "en": "the and of to in for with a is are you our we will your or as be on that this experience team work skills role responsibilities "
          "requirements looking join".split(),
    "pt": "de e o a para com em uma os as do da que não você nosso equipe experiência trabalho vaga requisitos responsabilidades empresa "
          "conhecimento".split(),
    "fr": "de la le et les des en pour avec une un du vous nous est dans sur expérience équipe poste mission profil entreprise".split(),
    "de": "und der die das mit für von ein eine zu den im wir sie ist erfahrung aufgaben team stelle unser bei auf".split(),
    "it": "di e il la per con una un che i le del della nel nostro esperienza lavoro sei siamo requisiti azienda".split(),
}
_STOP_SETS = {k: set(v) for k, v in _STOP.items()}
# palabras que solo existen en un idioma (desempatan es/pt, es/it, etc.)
_UNIQUE = {"es": {"para", "los", "las", "una", "años", "con", "su", "nuestro", "buscamos", "ofrecemos"},
           "pt": {"você", "não", "uma", "experiência", "vaga", "nosso", "equipe", "conhecimento"},
           "en": {"the", "and", "you", "our", "we", "your", "with", "will"},
           "fr": {"vous", "nous", "des", "une", "est", "dans"}, "de": {"und", "der", "die", "das", "wir", "sie"},
           "it": {"della", "nel", "nostro", "siamo", "sei"}}


def detect_language(text: str | None, min_tokens: int = 12) -> str | None:
    """Idioma por frecuencia de palabras funcionales. None si el texto es muy corto o ambiguo."""
    tokens = re.findall(r"[a-záéíóúñüçãõâêôàèìòùäöß]+", (text or "")[:3000].lower())
    if len(tokens) < min_tokens:
        return None
    score = {}
    for lang, stop in _STOP_SETS.items():
        hits = [t for t in tokens if t in stop]
        score[lang] = len(hits) + 2 * sum(1 for t in hits if t in _UNIQUE[lang])
    ranked = sorted(score.items(), key=lambda kv: -kv[1])
    (best, s1), (_, s2) = ranked[0], ranked[1]
    if s1 < 4 or s1 < 1.25 * s2:
        return None
    return best


# ---------------------------------------------------------------- fechas
_MONTHS = {
    "ene": 1, "jan": 1, "enero": 1, "january": 1, "feb": 2, "febrero": 2, "february": 2, "mar": 3, "marzo": 3, "march": 3,
    "abr": 4, "apr": 4, "abril": 4, "april": 4, "may": 5, "mayo": 5, "jun": 6, "junio": 6, "june": 6, "jul": 7, "julio": 7, "july": 7,
    "ago": 8, "aug": 8, "agosto": 8, "august": 8, "sep": 9, "sept": 9, "septiembre": 9, "setiembre": 9, "september": 9,
    "oct": 10, "octubre": 10, "october": 10, "nov": 11, "noviembre": 11, "november": 11, "dic": 12, "dec": 12, "diciembre": 12, "december": 12,
}
_UNITS = {"minuto": 0, "minute": 0, "min": 0, "hora": 0, "hour": 0, "hr": 0, "día": 1, "dia": 1, "day": 1, "semana": 7, "week": 7,
          "mes": 30, "month": 30, "año": 365, "year": 365}


def parse_date(value, today: date | None = None) -> str | None:
    """Cualquier formato habitual (ISO, epoch, RFC822, 'hace 3 días', '18 sept 2026', 'Posted Yesterday') -> 'YYYY-MM-DD'."""
    today = today or datetime.now(timezone.utc).date()
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r"\d{9,13}(\.\d+)?", value.strip())):
        n = float(value)
        n = n / 1000 if n > 1e11 else n
        return datetime.fromtimestamp(n, timezone.utc).date().isoformat()
    s = " ".join(str(value).split()).strip()
    low = s.lower()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    for fmt in ("%a %b %d %H:%M:%S %Z %Y",):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    if re.search(r"\b(hoy|today|just posted|recién|reciente|ahora|just now)\b", low):
        return today.isoformat()
    if re.search(r"\b(ayer|yesterday)\b", low):
        return (today - timedelta(days=1)).isoformat()
    m = re.search(r"(?:hace\s+)?(\d+)\+?\s*(minuto|minute|min|hora|hour|hr|d[ií]a|day|semana|week|mes|month|año|year)s?(?:es)?(?:\s+ago)?", low)
    if m and ("hace" in low or "ago" in low or "posted" in low or "publicad" in low):
        days = _UNITS.get(m.group(2), 0) * int(m.group(1))
        return (today - timedelta(days=days)).isoformat()
    m = re.search(r"(\d{1,2})\s*(?:de\s+)?([a-záéíóú]+)\.?,?\s*(?:de\s+)?(\d{4})", low)      # 18 sept 2026 / 18 de septiembre de 2026
    if m and m.group(2) in _MONTHS:
        return date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1))).isoformat()
    m = re.search(r"([a-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})", low)                                  # Oct 5, 2026
    if m and m.group(1) in _MONTHS:
        return date(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2))).isoformat()
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)                                   # dd/mm/yyyy (LATAM)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            return None
    return None


def is_expired(expires_at: str | None, today: date | None = None) -> bool:
    d = parse_date(expires_at) if expires_at else None
    return bool(d and d < (today or datetime.now(timezone.utc).date()).isoformat())


CLOSED_MARKERS = (
    "oferta cerrada", "vacante cerrada", "esta oferta ya no está disponible", "esta oferta ya no esta disponible",
    "oferta expirada", "la oferta ha expirado", "ya no se encuentra disponible", "vacante no disponible", "proceso finalizado",
    "no longer accepting applications", "this job is no longer available", "job has expired", "this position has been filled",
    "position is no longer available", "posting has expired", "job posting is closed", "no longer open",
)


def is_closed_text(text: str | None) -> bool:
    low = (text or "").lower()
    return any(m in low for m in CLOSED_MARKERS)


# ---------------------------------------------------------------- remoto / híbrido / contrato
_REMOTE = re.compile(r"\b(remot[oa]s?|teletrabajo|home\s*office|trabajo\s+(?:desde|en)\s+casa|work\s+from\s+home|wfh|100%\s*remote|fully\s+remote|telecommute|anywhere)\b", re.I)
_HYBRID = re.compile(r"\b(h[ií]brid[oa]s?|hybrid|semi\s*presencial|mixt[oa])\b", re.I)
_ONSITE = re.compile(r"\b(presencial|on[\s-]?site|in[\s-]?office|en\s+oficina|en\s+sitio)\b", re.I)


def detect_work_mode(*texts: str | None, remote_flag: bool | None = None) -> str | None:
    """remote | hybrid | onsite | None. Híbrido gana a remoto (\"remoto 2 días\" suele ser híbrido)."""
    blob = " ".join(t for t in texts if t)
    if _HYBRID.search(blob):
        return "hybrid"
    if remote_flag or _REMOTE.search(blob):
        return "remote"
    if _ONSITE.search(blob):
        return "onsite"
    return None


def normalize_employment(value: str | list | None) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    v = re.sub(r"[\s_-]+", "", (value or "").lower())
    for needle, out in (("fulltime", "full_time"), ("tiempocompleto", "full_time"), ("permanent", "full_time"), ("parttime", "part_time"),
                        ("mediotiempo", "part_time"), ("tiempoparcial", "part_time"), ("contract", "contract"), ("contractor", "contract"),
                        ("freelance", "contract"), ("temporary", "temporary"), ("temporal", "temporary"), ("intern", "internship"),
                        ("practic", "internship"), ("pasant", "internship")):
        if needle in v:
            return out
    return None


# ---------------------------------------------------------------- salario
_SYMBOL_CUR = [("us$", "USD"), ("usd", "USD"), ("u$s", "USD"), ("r$", "BRL"), ("s/", "PEN"), ("€", "EUR"), ("eur", "EUR"), ("£", "GBP"),
               ("gbp", "GBP"), ("cop", "COP"), ("mxn", "MXN"), ("ars", "ARS"), ("clp", "CLP"), ("pen", "PEN"), ("brl", "BRL"),
               ("uyu", "UYU"), ("crc", "CRC"), ("dop", "DOP"), ("gtq", "GTQ"), ("cad", "CAD"), ("pyg", "PYG"), ("bob", "BOB"), ("ves", "VES")]
_PERIODS = [("hour", r"por hora|per hour|/\s*h(?:ou)?r|hourly|la hora|/hora|por h\b"), ("day", r"por d[ií]a|per day|daily|diari[oa]|/d[ií]a"),
            ("week", r"por semana|per week|weekly|semanal|/semana|/week"),
            ("month", r"por mes|al mes|mensual|per month|monthly|a month|/mes|/month|/mo\b"),
            ("year", r"por a[ñn]o|al a[ñn]o|anual|per year|yearly|per annum|annually|/a[ñn]o|/year|/yr|a year|p\.a\.")]
_NUM = re.compile(r"(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)\s*(k|mil|m|millones|millón|mm)?\b", re.I)
_RANGE_SEP = re.compile(r"^\s*(?:-|–|—|a|to|hasta|y|and|/)\s*(?:us\$|\$|€|£|r\$|s/)?\s*$", re.I)
_SALARY_WORDS = re.compile(r"salari|sueldo|salary|compensation|remuneraci|pago|paga|ingreso|honorarios|\$|€|£|usd|cop|mxn|\bpesos\b", re.I)


def _to_number(raw: str, suffix: str | None) -> float:
    s = raw
    if "." in s and "," in s:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        s = s.replace("," if dec == "." else ".", "").replace(dec, ".")
    elif "." in s or "," in s:
        sep = "." if "." in s else ","
        parts = s.split(sep)
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3 and not suffix):
            s = s.replace(sep, "")                      # separador de miles
        else:
            s = s.replace(sep, ".")
    n = float(s)
    mult = {"k": 1e3, "mil": 1e3, "m": 1e6, "mm": 1e6, "millones": 1e6, "millón": 1e6}.get((suffix or "").lower(), 1)
    return n * mult


def parse_salary(text: str | None, country: str | None = None, require_marker: bool = False) -> dict | None:
    """Devuelve {min, max, currency, period} o None. `require_marker` exige símbolo/palabra de salario
    (para no confundir '3 años de experiencia' con dinero cuando se parsea una descripción)."""
    if not text:
        return None
    low = text.lower()
    nums = []
    for m in _NUM.finditer(text):
        try:
            nums.append((m.start(), m.end(), _to_number(m.group(1), m.group(2))))
        except ValueError:
            continue
    if not nums:
        return None
    currency = next((c for sym, c in _SYMBOL_CUR if sym in low), None)
    has_dollar = "$" in text
    if require_marker and not (currency or has_dollar or "€" in text or "£" in text or _SALARY_WORDS.search(low)):
        return None
    if not currency and has_dollar:
        currency = default_currency(country) or "USD"
    period = next((p for p, rx in _PERIODS if re.search(rx, low)), None)
    # primer par (a, b) unidos por separador de rango; si no, un solo valor
    first = nums[0]
    pair = None
    if len(nums) > 1 and _RANGE_SEP.match(text[nums[0][1]: nums[1][0]]):
        pair = (nums[0][2], nums[1][2])
    lo, hi = pair if pair else (first[2], first[2])
    lo, hi = min(lo, hi), max(lo, hi)
    floor = 5 if period == "hour" else 100            # descarta '2 años', '5 días'
    if hi < floor:
        return None
    if not currency:
        currency = default_currency(country)
    return {"min": lo, "max": hi, "currency": currency, "period": period}


# ---------------------------------------------------------------- finalización
def finalize(job: Job, *, default_country: str | None = None, today: date | None = None) -> Job:
    """Rellena los campos normalizados que la fuente no entregó. Idempotente."""
    today = today or datetime.now(timezone.utc).date()
    job.posted_at = parse_date(job.posted_at, today)
    job.expires_at = parse_date(job.expires_at, today)
    if not job.language:
        job.language = detect_language(f"{job.title}. {job.description}")
    if not job.country:
        job.country = guess_country(job.location) or (default_country if default_country in COUNTRIES else None)
    if not job.work_mode:
        job.work_mode = detect_work_mode(job.title, job.location, job.description[:1500], remote_flag=job.remote)
    if job.work_mode == "remote":
        job.remote = True
    if job.salary_min is None and job.salary_max is None:
        info = parse_salary(job.salary, job.country) if job.salary else None
        info = info or parse_salary(_salary_sentence(job.description), job.country, require_marker=True)
        if info:
            job.salary_min, job.salary_max = info["min"], info["max"]
            job.salary_currency = job.salary_currency or info["currency"]
            job.salary_period = job.salary_period or info["period"]
    if not job.salary and job.salary_min is not None:
        job.salary = f"{job.salary_min:g}-{job.salary_max:g} {job.salary_currency or ''}".strip()
    annualize(job)
    html = job.raw.pop("html", None) if job.raw else None
    if job.description_md is None and html:
        job.description_md = html_to_markdown(strip_hidden_html(html, looks_like_instruction)) or None   # sin texto oculto (app/safety)
    job.employment_type = normalize_employment(job.employment_type) or job.employment_type
    if not job.closed and (is_expired(job.expires_at, today) or is_closed_text(f"{job.title} {job.description[:400]}")):
        job.closed = True
    return job


_SAL_SENT = re.compile(r"[^.\n]{0,80}(?:salari|sueldo|salary|compensation|remuneraci|ofrecemos \$|pago)[^.\n]{0,120}", re.I)


def _salary_sentence(description: str) -> str | None:
    m = _SAL_SENT.search(description or "")
    return m.group(0) if m else None


# ---------------------------------------------------------------- salario anualizado y comparable
# Convención laboral estándar (la misma que usan los agregadores): 40 h/semana x 52 semanas = 2080 h; 260 días hábiles.
ANNUAL_FACTOR = {"hour": 2080, "day": 260, "week": 52, "month": 12, "year": 1}
_HARD_CURRENCIES = {"USD", "CAD", "EUR", "GBP", "AUD", "CHF"}
_LATAM_LOCAL = {"COP", "MXN", "ARS", "CLP", "PEN", "CRC", "UYU", "BOB", "GTQ", "DOP", "PYG", "HNL", "NIO", "VES", "BRL"}


def infer_period(amount: float, currency: str | None, country: str | None) -> str | None:
    """Periodo de un salario SIN periodo explícito. Conservador: devuelve None si es ambiguo.
    * USD/CAD/EUR/GBP/AUD/CHF: <= 250 -> hora; >= 20.000 -> año; entre medias es ambiguo (mensual vs anual parcial) -> None.
    * Monedas locales de LATAM: el mercado cotiza en MENSUAL -> mes (si el monto es >= 200).
    El resultado se marca con `salary_period_inferred=True`. NO se convierte entre monedas."""
    cur = (currency or "").upper()
    if cur in _HARD_CURRENCIES:
        return "hour" if amount <= 250 else "year" if amount >= 20_000 else None
    if cur in _LATAM_LOCAL or (not cur and (country or "").upper() in LATAM):
        return "month" if amount >= 200 else None
    return None


def annualize(job: Job) -> Job:
    """Rellena salary_annual_min/max (misma moneda que salary_currency). Idempotente. Sin tasa de cambio: solo etiqueta."""
    if job.salary_min is None and job.salary_max is None:
        return job
    lo = job.salary_min if job.salary_min is not None else job.salary_max
    hi = job.salary_max if job.salary_max is not None else job.salary_min
    period = job.salary_period
    if period not in ANNUAL_FACTOR:
        period = infer_period(hi, job.salary_currency, job.country)
        if period:
            job.salary_period, job.salary_period_inferred = period, True
    if period in ANNUAL_FACTOR:
        f = ANNUAL_FACTOR[period]
        if hi * f < 1e10:
            job.salary_annual_min, job.salary_annual_max = round(lo * f, 2), round(hi * f, 2)
    return job


# ---------------------------------------------------------------- filtros (antigüedad, contrato, modalidad)
def max_age_days(hours_old: int | None) -> int | None:
    """hours_old -> días. Las fechas de las ofertas tienen granularidad de DÍA, así que se redondea hacia arriba."""
    return None if hours_old is None else max(1, math.ceil(hours_old / 24))


def is_too_old(job: Job, hours_old: int | None, today: date | None = None, *, strict: bool = False) -> bool:
    days = max_age_days(hours_old)
    if days is None:
        return False
    d = parse_date(job.posted_at, today)
    if not d:
        return strict                       # sin fecha: se conserva salvo filtro estricto
    return d < ((today or datetime.now(timezone.utc).date()) - timedelta(days=days)).isoformat()


def matches_filters(job: Job, q: SearchQuery, today: date | None = None) -> bool:
    """Filtro LOCAL (siempre se aplica, aunque la fuente haya filtrado de forma nativa): antigüedad, contrato y modalidad.
    Un dato desconocido NO descarta la oferta salvo `strict_filters=True`."""
    if is_too_old(job, q.hours_old, today, strict=q.strict_filters):
        return False
    wanted = {normalize_employment(t) or t for t in q.employment_types}
    if wanted:
        et = normalize_employment(job.employment_type) or job.employment_type
        if et and et not in wanted:
            return False
        if not et and q.strict_filters:
            return False
    modes = set(q.work_modes)
    if modes:
        mode = job.work_mode or ("remote" if job.remote else None)
        if mode and mode not in modes:
            return False
        if not mode and q.strict_filters:
            return False
    return True
