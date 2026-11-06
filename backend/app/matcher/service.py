"""Orquestación de las dos etapas del matcher.

Etapa 1 (sin LLM): `build_scorer` -> `Stage1Scorer.score` para TODAS las ofertas nuevas.
Etapa 2 (LLM, 10-60 s por llamada): `analyze_job` solo bajo demanda del usuario o para las top-K de `pick_for_stage2`."""
from __future__ import annotations

from collections.abc import Iterable

from sqlmodel import Session

from app.ats.gaps import classify, extract_requirements, match_score
from app.domain.job import Job
from app.domain.profile import Profile
from app.domain.tailoring import KeywordGap
from app.llm.client import LLMClient
from app.matcher.hybrid import Stage1Scorer
from app.rag.retrieve import ProfileRetriever
from app.rag.service import retriever_for
from app.text import clean_title


def build_scorer(session: Session, user_id: int, profile: Profile, target_roles: list[str] | None = None) -> Stage1Scorer:
    """Scorer de etapa 1 con el árbol vigente del usuario (si los embeddings fallan, queda en modo léxico)."""
    return Stage1Scorer(profile, retriever_for(session, user_id, profile), target_roles)


def pick_for_stage2(scored: Iterable[tuple[int, float]], k: int = 10, min_stage1: float = 25.0) -> list[int]:
    """ids de las k mejores ofertas por puntaje de etapa 1 que superan el mínimo (candidatas a análisis LLM)."""
    ranked = sorted(((i, s) for i, s in scored if s >= min_stage1), key=lambda t: -t[1])
    return [i for i, _ in ranked[:k]]


def analyze_job(job: Job, profile: Profile, llm: LLMClient, confirmed: set[str] | None = None,
                retriever: ProfileRetriever | None = None) -> tuple[list[KeywordGap], int]:
    """Etapa 2: requisitos de la oferta + brechas contra el perfil (con evidencia recuperada del árbol)."""
    gaps = classify(extract_requirements(job, llm), profile, llm, confirmed, clean_title(job.title), retriever)
    return gaps, match_score(gaps)
