"""Calibración del ranking contra ofertas REALES etiquetadas (tests/fixtures_rank/*.json).
Mide lo que importa al usuario: que lo relevante quede arriba y lo ajeno se recomiende `skip`.
Usa el mismo código de producción (Stage1Scorer + evaluation.engine)."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session

from app.domain.job import Job
from app.domain.profile import Profile, SearchPreferences


@dataclass
class Scored:
    id: int
    title: str
    label: int
    stage1: float
    score: float
    rec: str


def load(path: str | Path) -> tuple[Profile, list[dict]]:
    d = json.loads(Path(path).read_text())
    prof = Profile.model_validate({**d["profile"], "preferences": SearchPreferences.model_validate(d["preferences"]).model_dump()})
    return prof, d["jobs"]


def score_all(profile: Profile, rows: list[dict], session: Session, user_id: int) -> list[Scored]:
    from app.evaluation import engine
    from app.matcher.service import build_scorer

    jobs = [Job(title=r["title"], company=r["company"], location=r["location"], description=r["description"], url=r["url"] or f"https://x/{r['id']}",
                source=r["source"] or "fixture") for r in rows]
    scorer = build_scorer(session, user_id, profile, profile.preferences.target_roles)
    scorer.prefetch(jobs)
    out = []
    for r, j in zip(rows, jobs, strict=True):
        s1 = scorer.score(j).score
        ev = engine.evaluate(j, profile, stage1=s1)
        out.append(Scored(r["id"], r["title"], r["label"], s1, ev.score, ev.recommendation))
    return out


def auc(scored: list[Scored], key: str, min_label: int = 1) -> float:
    """P(una oferta relevante puntúa más que una irrelevante). 0.5 = azar, 1.0 = separación perfecta."""
    pos = [getattr(s, key) for s in scored if s.label >= min_label]
    neg = [getattr(s, key) for s in scored if s.label == 0]
    if not pos or not neg:
        return float("nan")
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def ndcg(scored: list[Scored], key: str, k: int = 10) -> float:
    ranked = sorted(scored, key=lambda s: -getattr(s, key))[:k]
    dcg = sum((2 ** s.label - 1) / math.log2(i + 2) for i, s in enumerate(ranked))
    ideal = sorted((s.label for s in scored), reverse=True)[:k]
    idcg = sum((2 ** lab - 1) / math.log2(i + 2) for i, lab in enumerate(ideal))
    return dcg / idcg if idcg else float("nan")


def rec_quality(scored: list[Scored]) -> dict:
    """Lo que ve el usuario: de lo ajeno, cuánto NO se descarta; de lo claramente relevante, cuánto se descarta por error."""
    irr = [s for s in scored if s.label == 0]
    rel = [s for s in scored if s.label == 2]
    return {"irrelevant_not_skipped": sum(s.rec != "skip" for s in irr) / len(irr) if irr else float("nan"),
            "relevant_skipped": sum(s.rec == "skip" for s in rel) / len(rel) if rel else float("nan"),
            "apply_precision": (sum(s.label >= 1 for s in scored if s.rec == "apply") / max(1, sum(s.rec == "apply" for s in scored)))}


def report(scored: list[Scored]) -> dict:
    return {"auc_stage1": round(auc(scored, "stage1"), 3), "auc_score": round(auc(scored, "score"), 3),
            "ndcg10_stage1": round(ndcg(scored, "stage1"), 3), "ndcg10_score": round(ndcg(scored, "score"), 3),
            **{k: round(v, 3) for k, v in rec_quality(scored).items()}}
