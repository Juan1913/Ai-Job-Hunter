"""Fechas de CV: reconocer rangos ('Mar 2021 - Actualidad', '03/2019 – 12/2020', '2018-2020') y normalizarlas.

Formato canónico en el perfil: 'YYYY-MM' o 'YYYY'; fin vigente = None ('Actualidad/Present')."""
import re

from app.text import norm

MONTHS = {
    "ene": 1, "enero": 1, "jan": 1, "january": 1, "feb": 2, "febrero": 2, "february": 2, "mar": 3, "marzo": 3, "march": 3,
    "abr": 4, "abril": 4, "apr": 4, "april": 4, "may": 5, "mayo": 5, "jun": 6, "junio": 6, "june": 6, "jul": 7, "julio": 7, "july": 7,
    "ago": 8, "agosto": 8, "aug": 8, "august": 8, "sep": 9, "sept": 9, "set": 9, "septiembre": 9, "setiembre": 9, "september": 9,
    "oct": 10, "octubre": 10, "october": 10, "nov": 11, "noviembre": 11, "november": 11, "dic": 12, "diciembre": 12, "dec": 12, "december": 12,
}
PRESENT = ("actualidad", "presente", "actual", "present", "current", "currently", "now", "hoy", "al presente", "a la fecha", "hasta la fecha",
           "la actualidad", "la fecha", "en curso", "ongoing", "to date", "date", "today", "vigente", "actualmente")
_MON = "|".join(sorted(MONTHS, key=len, reverse=True))
_YEAR = r"(?:19|20)\d{2}"
_DATE = rf"(?:(?:(?:{_MON})\.?\s*(?:de\s+|del\s+|,\s*|')?|\d{{1,2}}\s*[/.\-]\s*){_YEAR}|{_YEAR}(?:\s*[/.\-]\s*\d{{1,2}})?)"
_PRES = "|".join(sorted((re.escape(p) for p in PRESENT), key=len, reverse=True))
_SEP = r"\s*(?:-|–|—|−|a|al|hasta|to|until|through|/)\s*"
RANGE_RE = re.compile(rf"(?P<s>{_DATE}){_SEP}(?P<e>{_DATE}|(?:{_PRES})\b)", re.IGNORECASE)
SINGLE_RE = re.compile(rf"(?<![\d/.\-])(?P<d>{_DATE})(?![\d])", re.IGNORECASE)

def parse_date(s: str | None) -> str | None:
    """'Mar 2021' -> '2021-03'; '03/2019' -> '2019-03'; '2018' -> '2018'; presente/vacío -> None."""
    if not s:
        return None
    t = norm(str(s)).strip(" .,;()")
    if not t or any(t == norm(p) for p in PRESENT):
        return None
    m = re.fullmatch(rf"({_YEAR})(?:\s*[/.\-]\s*(\d{{1,2}}))?", t)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}" if m.group(2) and 1 <= int(m.group(2)) <= 12 else m.group(1)
    m = re.fullmatch(rf"(\d{{1,2}})\s*[/.\-]\s*({_YEAR})", t)
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    m = re.fullmatch(rf"([a-z]+)\.?\s*(?:de\s+|del\s+|,\s*|')?({_YEAR})", t)
    if m and m.group(1) in MONTHS:
        return f"{m.group(2)}-{MONTHS[m.group(1)]:02d}"
    m = re.search(_YEAR, t)
    return m.group(0) if m else None

def is_present(s: str | None) -> bool:
    t = norm(s or "").strip(" .,;()")
    return any(t == norm(p) for p in PRESENT)

def find_range(line: str) -> tuple[str | None, str | None, tuple[int, int]] | None:
    """Primer rango de fechas de la línea: (inicio, fin|None si vigente, posición). None si no hay."""
    m = RANGE_RE.search(line)
    if m:
        s, e = parse_date(m.group("s")), m.group("e")
        if s:
            end = None if is_present(e) else parse_date(e)
            if end is None and not is_present(e):
                return None
            if end and s > end:      # rango invertido o falso positivo
                return None
            return s, end, m.span()
    return None

def find_single(line: str) -> tuple[str, tuple[int, int]] | None:
    m = SINGLE_RE.search(line)
    return (parse_date(m.group("d")) or "", m.span()) if m else None

_MES_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
_MES_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

def format_date(value: str | None, lang: str = "es") -> str:
    """Fecha legible para ATS ('Mar 2021', 'mar 2021', '2021'); acepta texto libre del usuario."""
    d = parse_date(value)
    if not d:
        return (value or "").strip()
    if "-" in d:
        y, m = d.split("-")
        return f"{(_MES_ES if lang == 'es' else _MES_EN)[int(m) - 1].capitalize()} {y}"
    return d
