"""Recuperación multinivel sobre el árbol del perfil.

Dado un requisito/oferta devuelve EVIDENCIAS (hojas, texto literal del perfil) + CONTEXTO (su nodo padre y la raíz).
Puntaje de hoja = coseno(hoja) + 0.15 * coseno(padre)  [arriba->abajo: una hoja de una experiencia relevante sube]
                + 0.25 * solapamiento léxico            [cubre términos exactos que el embedding diluye: 'Splunk']."""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from app.rag.embeddings import Embedder, scale_sim
from app.rag.store import Node, ProfileIndex
from app.text import norm

W_PARENT = 0.15
W_LEX = 0.25
_STOP = {"de", "la", "el", "en", "y", "a", "los", "las", "con", "para", "por", "un", "una", "del", "the", "and", "of", "to", "in", "for", "or", "with"}


def _stems(s: str) -> set[str]:
    """Raíces de 5 caracteres: 'incidentes' ~ 'incidents', 'monitoreo' ~ 'monitoring'."""
    return {t[:5] for t in re.findall(r"[a-z0-9+#&]{2,}", norm(s)) if t not in _STOP}


def _lex(query_stems: set[str], text: str) -> float:
    return len(query_stems & _stems(text)) / len(query_stems) if query_stems else 0.0


@dataclass
class Evidence:
    text: str            # literal del perfil (citable)
    ref: str
    kind: str
    score: float
    context: str         # resumen del nodo padre (experiencia/área): solo orientación, no citable
    root: str            # resumen del perfil


class ProfileRetriever:
    def __init__(self, index: ProfileIndex, embedder: Embedder):
        self.index, self.embedder = index, embedder

    def _score_leaves(self, q: np.ndarray, query_text: str) -> list[tuple[float, Node]]:
        cos = self.index.cosine(q)
        qs = _stems(query_text)
        out = []
        for i in self.index.leaf_idx:
            n = self.index.nodes[i]
            p = self.index.parent(n)
            pc = float(cos[self.index.pos[p.id]]) if p else 0.0
            out.append((float(cos[i]) + W_PARENT * pc + W_LEX * _lex(qs, n.text), n))
        out.sort(key=lambda t: -t[0])
        return out

    def _evidence(self, scored: list[tuple[float, Node]], k: int) -> list[Evidence]:
        root = self.index.root
        res = []
        for sc, n in scored[:k]:
            p = self.index.parent(n)
            res.append(Evidence(n.text, n.ref, n.kind, round(sc, 4), p.text if p else "", root.text if root else ""))
        return res

    def evidence(self, query: str, k: int = 3) -> list[Evidence]:
        return self.evidence_many([query], k)[0]

    def evidence_many(self, queries: list[str], k: int = 3) -> list[list[Evidence]]:
        """Un solo lote de embeddings para todos los requisitos de una oferta."""
        if not queries or not self.index.nodes:
            return [[] for _ in queries]
        qv = self.embedder.embed(queries, "query")
        return [self._evidence(self._score_leaves(qv[i], q), k) for i, q in enumerate(queries)]

    def similarity(self, text_vec: np.ndarray, topk: int = 3) -> tuple[float, float]:
        """(sim con la raíz, media de las top-k hojas) ya escaladas a 0..1 según el modelo."""
        cos = self.index.cosine(text_vec)
        root = self.index.root
        ri = self.index.pos[root.id] if root else None
        rs = scale_sim(self.embedder, float(cos[ri])) if ri is not None else 0.0
        leaves = sorted((float(cos[i]) for i in self.index.leaf_idx), reverse=True)[:topk]
        ls = scale_sim(self.embedder, float(np.mean(leaves))) if leaves else 0.0
        return rs, ls
