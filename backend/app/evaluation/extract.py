"""Extracción determinista (regex ES/EN) de requisitos de seniority, años e idiomas de una oferta, y derivación de lo mismo
desde el perfil. El LLM solo entra (opcional, cacheado) cuando las regex no concluyen: ver engine.refine_with_llm."""
from __future__ import annotations

import re
from datetime import date

from app.domain.job import Job
from app.domain.profile import Profile
from app.profile import dates
from app.text import norm

LEVELS = ["intern", "junior", "mid", "senior", "lead", "director"]
LEVEL_YEARS = {"intern": 0, "junior": 1, "mid": 3, "senior": 5, "lead": 7, "director": 10}   # años típicos si la oferta no los dice

_LEVEL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("director", re.compile(r"\b(director|directora|vp|vice ?president|vicepresident[ea]|head of|jefe de area|c[- ]?level|cto|cio|ciso)\b")),
    ("lead", re.compile(r"\b(lead|lider|team lead|tech lead|principal|staff|manager|gerente|jefe|coordinador|coordinadora|supervisor|supervisora|arquitect[oa]|architect)\b")),
    ("senior", re.compile(r"\b(senior|sr\.?|sénior|senor|experto|expert)\b")),
    ("mid", re.compile(r"\b(semi ?senior|semisenior|ssr|mid[- ]?level|mid|intermedio|intermedia|pleno|plena|nivel medio)\b")),
    ("junior", re.compile(r"\b(junior|jr\.?|entry[- ]?level|sin experiencia|recien egresad[oa]|egresad[oa]|graduate|associate|asistente|auxiliar|analista jr)\b")),
    ("intern", re.compile(r"\b(practicante|pasante|intern|internship|trainee|becari[oa]|aprendiz|apprentice|practicas|pr[aá]cticas)\b")),
]
_PREF = re.compile(r"(deseable|preferible|preferred|nice to have|a plus|es un plus|valorable|se valora|ideal|bonus|opcional|optional|ventaja)")
_YEARS = [
    re.compile(r"(\d{1,2})\s*(?:\+|o m[aá]s|or more)?\s*(?:a|-|–|to)\s*(\d{1,2})\s*(?:\+)?\s*(?:a[nñ]os?|years?|yrs?)"),
    re.compile(r"(?:m[ií]nimo|al menos|minimum(?: of)?|at least|min\.?)?\s*(\d{1,2})\s*\+?\s*(?:a[nñ]os?|years?|yrs?)"),
]
_EXP_NEAR = re.compile(r"(experienc|experience|trayectoria|track record|background|desempe)")
_NOT_REQ = re.compile(r"(fundad|founded|mercado|market|years? ago|a[nñ]os de historia|history|in business|trayectoria de la empresa|somos|we have been|since)")

_LANGS = {"en": r"ingl[eé]s|english", "es": r"espa[nñ]ol|spanish|castellano", "pt": r"portugu[eé]s|portuguese", "fr": r"franc[eé]s|french",
          "de": r"alem[aá]n|german|deutsch", "it": r"italian[oa]?", "zh": r"chin[oa]|mandar[ií]n|mandarin|chinese", "ja": r"japon[eé]s|japanese"}
LANG_NAMES = {"en": ("inglés", "English"), "es": ("español", "Spanish"), "pt": ("portugués", "Portuguese"), "fr": ("francés", "French"),
              "de": ("alemán", "German"), "it": ("italiano", "Italian"), "zh": ("chino", "Chinese"), "ja": ("japonés", "Japanese")}
_CEFR = {"a1": 1, "a2": 2, "b1": 3, "b2": 4, "c1": 5, "c2": 6}
_LEVEL_WORDS = [(r"nativ[oa]|native|biling[uü]e|bilingual|mother tongue|lengua materna", 6), (r"c2|proficiency|maestr[ií]a", 6),
                (r"c1|fluido|fluente|fluent|advanced|avanzad[oa]|excelente|excellent|full professional|profesional|professional", 5),
                (r"b2|upper[- ]intermediate|intermedio[- ]alto|alto|good|buen[oa]?", 4), (r"b1|intermedio|intermediate|conversational|conversacional|medio", 3),
                (r"a2|b[aá]sico|basic|elemental|elementary|survival", 2), (r"a1|beginner|principiante", 1)]
_OPT_LANG = re.compile(r"(deseable|plus|valorable|preferible|preferred|nice to have|ventaja|optional|opcional|se valora|ideal)")


def detect_level(text: str) -> str | None:
    """Nivel por el título (prioritario) y, si no hay señal, por los primeros 600 caracteres de la descripción."""
    t = norm(text)
    for lv, rx in _LEVEL_PATTERNS:
        if rx.search(t):
            return lv
    return None


def job_level(job: Job) -> str | None:
    return detect_level(job.title) or detect_level(job.description[:600])


def required_years(text: str) -> tuple[float | None, str]:
    """Años de experiencia exigidos (máximo de los menciones NO opcionales). Devuelve (años, cita)."""
    t = norm(text)
    best: tuple[float, str] | None = None
    taken: list[tuple[int, int]] = []
    for rx in _YEARS:
        for m in rx.finditer(t):
            lo, hi = m.start(), m.end()
            if any(lo < b and a < hi for a, b in taken):
                continue                      # ya contado como rango («3 a 5 años» exige 3, no 5)
            near = t[max(0, lo - 45): hi + 45]
            if not _EXP_NEAR.search(near) or _NOT_REQ.search(t[max(0, lo - 40): hi + 15]):
                continue
            if _PREF.search(t[max(0, lo - 30): hi + 40]):
                continue
            taken.append((lo, hi))
            n = float(m.group(1))
            if 0 < n <= 25 and (best is None or n > best[0]):
                best = (n, t[max(0, lo - 70): hi + 70].strip())
    return (best[0], best[1]) if best else (None, "")


def language_level(text: str) -> int | None:
    t = norm(text)
    for m in re.finditer(r"\b(a1|a2|b1|b2|c1|c2)\b", t):
        return _CEFR[m.group(1)]
    for rx, lv in _LEVEL_WORDS:
        if re.search(rf"\b(?:{rx})\b", t):
            return lv
    return None


def required_languages(text: str, offer_lang: str | None = None) -> list[dict]:
    """Idiomas que la oferta exige: [{code, level(0-6|None), required(bool), quote}]. El idioma en que está escrita no cuenta."""
    t = norm(text)
    out: dict[str, dict] = {}
    for code, rx in _LANGS.items():
        for m in re.finditer(rf"\b(?:{rx})\b", t):
            a = max(t.rfind(".", 0, m.start()), t.rfind(";", 0, m.start()), t.rfind("\n", 0, m.start()), m.start() - 90) + 1
            nxt = [i for i in (t.find(".", m.end()), t.find(";", m.end()), t.find("\n", m.end())) if i != -1]
            ctx = t[a: min(min(nxt) if nxt else len(t), m.end() + 90)]
            if not _OPT_LANG.search(ctx) and not re.search(r"(nivel|level|idioma|language|fluen|fluid|avanz|advanced|intermed|basic|basico|bilingu|nativ|conversation|hablad|escrit|written|spoken|oral|b1|b2|c1|c2|a2|requer|requisit|requirement|dominio|manejo|speak|habla|comunicaci|communicat|profesional|professional|excellent|excelente|good|buen)", ctx):
                continue
            if code == (offer_lang or "") and not re.search(r"(nivel|level|b1|b2|c1|c2|avanz|advanced|nativ|bilingu)", ctx):
                continue            # «Spanish» en una oferta en español es el idioma de redacción, no un requisito
            lvl = language_level(norm(t[max(0, m.start() - 35): m.end() + 40]))
            req = not _OPT_LANG.search(ctx)
            cur = out.get(code)
            if cur is None or (lvl or 0) > (cur["level"] or 0) or (req and not cur["required"]):
                out[code] = {"code": code, "level": lvl, "required": req or (cur["required"] if cur else False), "quote": ctx.strip()}
    if re.search(r"\bbiling[uü]e\b|\bbilingual\b", t) and "en" not in out and offer_lang == "es":
        out["en"] = {"code": "en", "level": 5, "required": True, "quote": "bilingüe"}
    return list(out.values())


# ------------------------------------------------------------------ perfil
def _ym(s: str | None, end: bool, today: date) -> tuple[int, int] | None:
    d = dates.parse_date(s)
    if not d:
        return None
    y, _, m = d.partition("-")
    return (int(y), int(m)) if m else (int(y), 12 if end else 1)


def profile_years(profile: Profile, today: date | None = None) -> float | None:
    """Años de experiencia = unión de los intervalos de las experiencias (sin contar dos veces los solapados)."""
    today = today or date.today()
    spans: list[tuple[int, int]] = []
    for e in profile.experience:
        s = _ym(e.start, False, today)
        if s is None:
            continue
        en = _ym(e.end, True, today) if e.end and not dates.is_present(e.end) else (today.year, today.month)
        if en is None:
            en = (today.year, today.month)
        a, b = s[0] * 12 + s[1], min(en[0] * 12 + en[1], today.year * 12 + today.month)
        if b >= a:
            spans.append((a, b))
    if not spans:
        return None
    spans.sort()
    total, cur_a, cur_b = 0, spans[0][0], spans[0][1]
    for a, b in spans[1:]:
        if a <= cur_b:
            cur_b = max(cur_b, b)
        else:
            total += cur_b - cur_a
            cur_a, cur_b = a, b
    total += cur_b - cur_a
    return round(total / 12, 1)


def profile_level(profile: Profile, years: float | None) -> str | None:
    """Nivel del candidato: por sus cargos recientes si lo dicen, si no por años de experiencia."""
    titles = " ".join([profile.headline, *[e.title for e in profile.experience[:2]]])
    explicit = detect_level(titles)
    if explicit in ("lead", "director", "senior"):
        return explicit
    if years is None:
        return explicit
    for lv, upto in (("junior", 2.0), ("mid", 4.5)):
        if years < upto:
            return lv if years >= 0.5 else "intern"
    return "senior"


def profile_languages(profile: Profile) -> dict[str, int | None]:
    """{código: nivel (0-6) | None si no declaró nivel}. Lee «Inglés (B2)», «English - Advanced», «Español nativo»..."""
    out: dict[str, int | None] = {}
    for raw in profile.languages:
        t = norm(raw)
        for code, rx in _LANGS.items():
            if re.search(rf"\b(?:{rx})\b", t):
                out[code] = language_level(t)
    return out
