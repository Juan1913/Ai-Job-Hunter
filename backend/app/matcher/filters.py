"""Filtros duros baratos de la etapa 1: país, idioma y modalidad remota (sin LLM)."""
from __future__ import annotations

import re

from app.domain.geo import COUNTRY_NAMES
from app.domain.job import Job
from app.domain.profile import SearchPreferences
from app.text import norm

# Nombres/alias/ciudades -> ISO. Se busca por palabra completa sobre job.location normalizado.
_ALIASES: dict[str, list[str]] = {
    "CO": ["colombia", "bogota", "medellin", "cali", "barranquilla", "bucaramanga"],
    "MX": ["mexico", "cdmx", "ciudad de mexico", "guadalajara", "monterrey"],
    "AR": ["argentina", "buenos aires", "cordoba", "rosario"],
    "CL": ["chile", "santiago"],
    "PE": ["peru", "lima"],
    "EC": ["ecuador", "quito", "guayaquil"],
    "ES": ["espana", "spain", "madrid", "barcelona", "valencia", "sevilla"],
    "US": ["united states", "usa", "u.s.", "estados unidos", "new york", "san francisco", "austin", "seattle"],
    "BR": ["brasil", "brazil", "sao paulo", "rio de janeiro"],
    "UY": ["uruguay", "montevideo"],
    "PA": ["panama"],
    "CR": ["costa rica", "san jose"],
    "VE": ["venezuela", "caracas"],
    "DO": ["republica dominicana", "dominican republic", "santo domingo"],
    "GT": ["guatemala"],
    "GB": ["united kingdom", "reino unido", "london", "londres", "uk"],
    "DE": ["germany", "alemania", "berlin"],
}
for _iso, _name in COUNTRY_NAMES.items():
    _ALIASES.setdefault(_iso, []).append(norm(_name))

_ES = {"de", "la", "el", "en", "y", "los", "las", "con", "para", "por", "una", "del", "que", "se", "experiencia", "requisitos", "trabajo", "ofrecemos", "empresa"}
_EN = {"the", "and", "of", "to", "in", "for", "with", "you", "we", "our", "is", "are", "experience", "requirements", "work", "will", "team", "ability"}


def countries_in(text: str) -> set[str]:
    t = norm(text)
    found = set()
    for iso, names in _ALIASES.items():
        if any(re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", t) for n in names):
            found.add(iso)
    return found


def detect_language(text: str) -> str | None:
    """'es' / 'en' por palabras funcionales; None si no hay señal suficiente."""
    words = re.findall(r"[a-z]+", norm(text)[:3000])
    es, en = sum(w in _ES for w in words), sum(w in _EN for w in words)
    if es + en < 4 or max(es, en) < 1.5 * min(es, en) + 1:
        return None
    return "es" if es > en else "en"


def hard_filters(job: Job, prefs: SearchPreferences) -> list[str]:
    """Motivos por los que la oferta queda fuera de lo que el usuario pidió (lista vacía = pasa)."""
    reasons: list[str] = []
    if job.remote and not prefs.remote:
        reasons.append("remota y el usuario no quiere remoto")
    if prefs.countries and not (job.remote and prefs.remote):
        wanted = {c.upper() for c in prefs.countries} | countries_in(" ".join(prefs.regions))
        seen = countries_in(job.location)
        if seen and not (seen & wanted):
            reasons.append(f"país fuera de lo pedido ({', '.join(sorted(seen))})")
    if prefs.languages:
        lang = detect_language(f"{job.title}. {job.description}")
        if lang and lang not in {x.lower()[:2] for x in prefs.languages}:
            reasons.append(f"idioma '{lang}' no deseado")
    return reasons
