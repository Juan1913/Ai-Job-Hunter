"""Redacción EXTRACTIVA del CV (sin LLM): no se reescribe nada, solo se SELECCIONA y ORDENA lo que ya está en el perfil.

- Viñetas reales (literales) elegidas y ordenadas por relevancia para la oferta; sin tocar empresas, cargos ni fechas.
- Habilidades respaldadas (tiene / adyacente con evidencia / confirmada) primero.
- Titular: el del perfil + cargo objetivo (regla de `tailor._assemble`).
- Resumen por plantilla ES/EN con datos del perfil (años calculados de sus fechas, rol, habilidades que SÍ tiene).
La regla de oro (`includable`) se respeta igual que en la ruta con LLM; después siguen la auditoría de fidelidad y el verificador ATS."""
from __future__ import annotations

import re

from app.ai.report import EXTRACTIVE_MARK
from app.cv.audit import experience_span_years
from app.cv.lang import detect_lang
from app.domain.job import Job
from app.domain.profile import Profile
from app.domain.tailoring import GapStatus, KeywordGap, TailoredCV, includable
from app.profile import dates
from app.rag.retrieve import _stems
from app.text import clean_title, covered, norm

MAX_BULLETS_RECENT = 6
MAX_BULLETS_OLDER = 4
MAX_SKILLS = 24


def profile_lang(profile: Profile) -> str:
    sample = " ".join([profile.summary, *[b for e in profile.experience for b in e.bullets][:8]]) or profile.headline
    return detect_lang(sample, "es")


def _bullet_score(bullet: str, backed: list[KeywordGap], job_stems: set[str]) -> float:
    b_n = norm(bullet)
    kw = sum((1.0 if g.status != GapStatus.ADJACENT else 0.8) for g in backed if covered(b_n, g.keyword))
    st = _stems(bullet)
    overlap = len(st & job_stems) / len(st) if st else 0.0
    return kw + 0.6 * overlap + (0.2 if re.search(r"\d", bullet) else 0.0)


def select_bullets(bullets: list[str], backed: list[KeywordGap], job_stems: set[str], limit: int) -> list[str]:
    if len(bullets) <= 1:
        return list(bullets)
    scored = sorted(enumerate(bullets), key=lambda t: (-_bullet_score(t[1], backed, job_stems), t[0]))
    keep = max(min(len(bullets), 2), min(len(bullets), limit))
    return [b for _, b in scored[:keep]]


def order_skills(profile: Profile, gaps: list[KeywordGap], job: Job) -> list[str]:
    """Primero las habilidades del perfil que casan con requisitos respaldados, luego las que la oferta menciona, luego el resto."""
    backed = [g for g in gaps if includable(g)]
    job_n = norm(f"{job.title} {job.description}")
    head, mid, tail = [], [], []
    for s in profile.skills:
        n = norm(s.name)
        if any(covered(n, g.keyword) or covered(norm(g.keyword), s.name) for g in backed):
            head.append(s.name)
        elif covered(job_n, s.name):
            mid.append(s.name)
        else:
            tail.append(s.name)
    return (head + mid + tail)[:MAX_SKILLS]


def _join(items: list[str], lang: str) -> str:
    conj = "y" if lang == "es" else "and"
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" {conj} " + items[-1]


def build_summary(profile: Profile, job: Job, gaps: list[KeywordGap], lang: str) -> str:
    """Resumen de plantilla: años (de las fechas del perfil), rol, habilidades que el candidato TIENE y su resumen literal."""
    es = lang == "es"
    title = clean_title(job.title)
    role = profile.headline or (profile.experience[0].title if profile.experience else "")
    years = int(experience_span_years(profile))
    have = [g.keyword for g in gaps if g.status == GapStatus.HAVE and norm(g.keyword) != norm(title) and len(g.keyword.split()) <= 4][:5]
    parts: list[str] = []
    own_claims_years = bool(re.search(r"\d+\s*\+?\s*(?:años|año|years?|yrs)\b", profile.summary, re.I))   # no contradecir la cifra del propio candidato
    if role and years >= 1 and not own_claims_years:
        parts.append(f"{role} con {years} años de experiencia profesional." if es else f"{role} with {years} years of professional experience.")
    elif role:
        parts.append(f"{role}." if es else f"{role}.")
    if have:
        parts.append(f"Experiencia en {_join(have, lang)}." if es else f"Experience in {_join(have, lang)}.")
    last = max(profile.experience, key=lambda e: dates.parse_date(e.start) or "", default=None)
    if last and last.title and last.company:
        parts.append(f"Último puesto: {last.title} en {last.company}." if es else f"Most recent role: {last.title} at {last.company}.")
    own = [x for x in re.split(r"(?<=[.!?])\s+", profile.summary.strip()) if x][:2]
    parts += own
    return " ".join(parts).strip()


def tailor_extractive(profile: Profile, job: Job, gaps: list[KeywordGap], reason: str = "mode_none") -> TailoredCV:
    from app.cv.tailor import _assemble  # import tardío: tailor importa este módulo en la ruta de respaldo

    lang = profile_lang(profile)
    backed = [g for g in gaps if includable(g)]
    job_stems = _stems(f"{job.title} {job.description}")
    recent = max(range(len(profile.experience)), key=lambda i: dates.parse_date(profile.experience[i].start) or "", default=0) if profile.experience else 0
    experience = [{"bullets": select_bullets(e.bullets, backed, job_stems, MAX_BULLETS_RECENT if i == recent else MAX_BULLETS_OLDER)}
                  for i, e in enumerate(profile.experience)]
    data = {"headline": profile.headline, "summary": build_summary(profile, job, gaps, lang),
            "skills": order_skills(profile, gaps, job), "experience": experience}
    cv = _assemble(profile, job, data, gaps)
    cv.trace = [f"{EXTRACTIVE_MARK} CV extractivo (sin reescritura): viñetas reales elegidas y ordenadas por relevancia, resumen por plantilla; motivo={reason}"]
    return cv
