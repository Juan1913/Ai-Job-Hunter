"""Etapa 1 híbrida del matcher: barata (sin LLM) y suficiente para ordenar cientos de ofertas.

    score = w_lex * léxico  +  w_sem * semántica  +  w_role * rol          (todo 0..100)
      léxico    app/matcher/lexical.stage1_score (habilidades/título/experiencia por tokens)
      semántica similitud de embeddings oferta<->árbol del perfil: mezcla de raíz (visión global) y top-3 hojas (evidencia)
      rol       similitud título de la oferta <-> roles objetivo (o titular del perfil)
    Filtros duros (país / idioma / remoto): la oferta no se descarta (el usuario puede querer verla), pero su puntaje
    se aplasta a <= FILTERED_CAP para que quede al fondo. Roles objetivo: penalización x0.6 si el título está lejos.

Calibración de pesos y rangos de coseno: ver tests/test_matcher_eval.py (dataset ES/EN) y el docstring de DEFAULT_WEIGHTS.
Si los embeddings fallan, cae al puntaje léxico (comportamiento previo) conservando los filtros."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import numpy as np

from app.domain.job import Job
from app.domain.profile import Profile
from app.matcher.filters import hard_filters
from app.matcher.lexical import stage1_score
from app.rag.embeddings import EmbeddingError, scale_sim
from app.rag.retrieve import ProfileRetriever
from app.text import norm, specific_tokens

log = logging.getLogger(__name__)

FILTERED_CAP = 5.0
ROLE_FLOOR = 0.15          # por debajo, el título no se parece a ningún rol objetivo
ROLE_PENALTY = 0.6


@dataclass(frozen=True)
class Weights:
    """Pesos calibrados sobre el dataset de evaluación (ver informe del matcher). Suman 1.
    El léxico pesa menos porque falla con sinónimos/idioma cruzado; los embeddings aportan la mayor parte."""
    lexical: float = 0.30
    semantic: float = 0.45
    role: float = 0.25
    root_vs_leaves: float = 0.5     # dentro de semántica: peso de la raíz (resto = hojas)


DEFAULT_WEIGHTS = Weights()


@dataclass
class Stage1Result:
    score: float
    lexical: float
    semantic: float | None = None
    role: float | None = None
    reasons: list[str] = field(default_factory=list)       # filtros duros / penalizaciones aplicadas

    @property
    def filtered(self) -> bool:
        return any(r.startswith("filtro:") for r in self.reasons)


def _job_text(job: Job) -> str:
    return f"{job.title}\n{job.description[:1800]}"


def _role_tokens_overlap(role: str, title: str) -> float:
    """Solapamiento por palabras que identifican el ÁREA ('soc', 'ciberseguridad'): 'analista' o 'senior' no cuentan.
    Si el rol solo tiene palabras genéricas, cae al solapamiento completo."""
    r, t = specific_tokens(role), specific_tokens(title)
    if r:
        return len(r & t) / len(r)
    full = lambda s: {x[:5] for x in re.findall(r"[a-z0-9+#&]{2,}", norm(s))}   # noqa: E731
    fr, ft = full(role), full(title)
    return len(fr & ft) / len(fr) if fr else 0.0


def _with_synonyms(roles: list[str], limit: int = 8) -> list[str]:
    """Roles objetivo + sus sinónimos ES/EN del diccionario curado (sin LLM): 'analista soc' reconoce 'security analyst'."""
    try:
        from app.domain.job import SearchQuery
        from app.scout.query import expand_query
        extra = expand_query(SearchQuery(keywords=list(roles)), use_llm=False).variants or []
    except Exception:  # noqa: BLE001 — los sinónimos son una mejora, nunca un requisito
        extra = []
    out: list[str] = []
    for r in [*roles, *extra]:
        if r and norm(r) not in {norm(x) for x in out}:
            out.append(r)
    return out[: len(roles) + limit]


class Stage1Scorer:
    """Puntúa ofertas de un usuario. Preparar con `prefetch(jobs)` para embeber en lotes."""

    def __init__(self, profile: Profile, retriever: ProfileRetriever | None = None,
                 target_roles: list[str] | None = None, weights: Weights = DEFAULT_WEIGHTS):
        self.profile, self.retriever, self.w = profile, retriever, weights
        base = [r for r in (profile.preferences.target_roles or target_roles or ([profile.headline] if profile.headline else [])) if r.strip()]
        self.roles = _with_synonyms(base) if base else base
        self.lex_roles = target_roles
        self._vec: dict[str, tuple[np.ndarray, np.ndarray]] = {}   # job.dedup_key -> (vec descripción, vec título)
        self._role_vec: np.ndarray | None = None

    def _embed_jobs(self, jobs: list[Job]) -> None:
        r = self.retriever
        todo = [j for j in jobs if j.dedup_key not in self._vec]
        if not r or not todo:
            return
        texts = [_job_text(j) for j in todo] + [j.title for j in todo]
        vecs = r.embedder.embed(texts, "query")
        n = len(todo)
        for i, j in enumerate(todo):
            self._vec[j.dedup_key] = (vecs[i], vecs[n + i])
        if self._role_vec is None and self.roles:
            self._role_vec = r.embedder.embed(self.roles, "query")

    def prefetch(self, jobs: list[Job]) -> None:
        try:
            self._embed_jobs(jobs)
        except (EmbeddingError, ValueError) as e:
            log.warning("etapa 1 sin embeddings (%s): solo léxico", e)
            self.retriever = None

    def score(self, job: Job) -> Stage1Result:
        lex = stage1_score(self.profile, job, self.lex_roles)
        reasons = [f"filtro: {m}" for m in hard_filters(job, self.profile.preferences)]
        sem = role = None
        if self.retriever is not None:
            try:
                self._embed_jobs([job])
                dvec, tvec = self._vec[job.dedup_key]
                rs, ls = self.retriever.similarity(dvec)
                sem = 100 * (self.w.root_vs_leaves * rs + (1 - self.w.root_vs_leaves) * ls)
                role = 100 * self._role_score(job, tvec)
            except (EmbeddingError, ValueError) as e:
                log.warning("etapa 1: embeddings no disponibles (%s)", e)
                self.retriever = None
        if sem is None:
            score = lex
        else:
            score = self.w.lexical * lex + self.w.semantic * sem + self.w.role * (role if role is not None else 0.0)
            if self.roles and role is not None and role / 100 < ROLE_FLOOR:
                reasons.append("penalización: título lejos de los roles objetivo")
                score *= ROLE_PENALTY
        if any(r.startswith("filtro:") for r in reasons):
            score = min(score, FILTERED_CAP)
        return Stage1Result(round(float(score), 1), lex, None if sem is None else round(sem, 1),
                            None if role is None else round(role, 1), reasons)

    def _role_score(self, job: Job, title_vec: np.ndarray) -> float:
        if not self.roles or self.retriever is None or self._role_vec is None:
            return 0.0
        best = 0.0
        for role, rv in zip(self.roles, self._role_vec, strict=False):
            sem = scale_sim(self.retriever.embedder, float(rv @ title_vec))
            best = max(best, 0.6 * sem + 0.4 * _role_tokens_overlap(role, job.title))
        return min(best, 1.0)


def stage1_hybrid(profile: Profile, job: Job, retriever: ProfileRetriever | None = None,
                  target_roles: list[str] | None = None) -> Stage1Result:
    """Atajo para un solo puntaje (los lotes deben usar Stage1Scorer + prefetch)."""
    return Stage1Scorer(profile, retriever, target_roles).score(job)
