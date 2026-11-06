"""Etapa 1 del matching: puntaje barato (sin LLM) para ordenar cientos de ofertas.
La etapa 2 (LLM + RAG) solo se aplica a las mejores o bajo demanda."""
import re

from app.domain.job import Job
from app.domain.profile import Profile
from app.text import norm

_STOP = {"de", "la", "el", "en", "y", "a", "los", "las", "con", "para", "por", "un", "una", "del", "the", "and", "of", "to", "in", "for", "or", "with"}


def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9+#&.]{2,}", norm(s)) if t not in _STOP}


def stage1_score(profile: Profile, job: Job, target_roles: list[str] | None = None) -> float:
    title_t, body_t = _tokens(job.title), _tokens(job.description[:4000])
    skills = {norm(s.name) for s in profile.skills}
    skill_hits = sum(1 for s in skills if s and (s in body_t or s in norm(job.title) or any(s == t for t in title_t | body_t)))
    skill_part = min(skill_hits / max(min(len(skills), 12), 1), 1.0)
    roles = target_roles or ([profile.headline] if profile.headline else [])
    role_t = set().union(*[_tokens(r) for r in roles]) if roles else set()
    title_part = len(role_t & title_t) / len(role_t) if role_t else 0.0
    exp_t = _tokens(" ".join(e.title for e in profile.experience))
    exp_part = len(exp_t & title_t) / len(title_t) if title_t else 0.0
    return round(100 * (0.45 * skill_part + 0.40 * title_part + 0.15 * exp_part), 1)
