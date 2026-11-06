"""Métricas de ranking para evaluar el matcher."""
import math


def dcg(rels: list[float], k: int) -> float:
    return sum((2 ** r - 1) / math.log2(i + 2) for i, r in enumerate(rels[:k]))


def ndcg_at_k(ranked_rels: list[float], k: int) -> float:
    """ranked_rels: relevancias en el orden que propuso el sistema."""
    ideal = dcg(sorted(ranked_rels, reverse=True), k)
    return dcg(ranked_rels, k) / ideal if ideal > 0 else 1.0


def recall_at_k(ranked_rels: list[float], k: int, min_rel: float = 2) -> float:
    total = sum(1 for r in ranked_rels if r >= min_rel)
    return sum(1 for r in ranked_rels[:k] if r >= min_rel) / total if total else 1.0
