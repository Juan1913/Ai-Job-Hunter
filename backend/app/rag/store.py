"""Persistencia del árbol del perfil en SQLite (tabla ProfileNode) y matriz de vectores en memoria (numpy)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from threading import Lock

import numpy as np
from sqlmodel import Session, delete, select

from app.db import ProfileNode
from app.domain.profile import Profile
from app.rag.embeddings import Embedder
from app.rag.tree import LEAF, build_nodes, profile_hash

log = logging.getLogger(__name__)
_rebuild_lock = Lock()


@dataclass
class Node:
    id: int
    level: int
    kind: str
    ref: str
    text: str
    parent_id: int | None
    vec: np.ndarray = field(repr=False)


@dataclass
class ProfileIndex:
    """Vista en memoria del árbol de un usuario: nodos + matriz (n, d) para coseno vectorizado."""
    nodes: list[Node]
    model: str
    profile_hash: str

    def __post_init__(self) -> None:
        self.by_id = {n.id: n for n in self.nodes}
        self.pos = {n.id: i for i, n in enumerate(self.nodes)}
        self.matrix = np.stack([n.vec for n in self.nodes]).astype(np.float32) if self.nodes else np.zeros((0, 0), dtype=np.float32)
        self.leaf_idx = [i for i, n in enumerate(self.nodes) if n.level == LEAF]

    @property
    def root(self) -> Node | None:
        return next((n for n in self.nodes if n.level == 2), None)

    def parent(self, n: Node) -> Node | None:
        return self.by_id.get(n.parent_id) if n.parent_id else None

    def cosine(self, q: np.ndarray) -> np.ndarray:
        """Coseno de un vector (d,) contra todos los nodos (filas ya normalizadas)."""
        if not len(self.nodes) or q.shape[-1] != self.matrix.shape[1]:
            raise ValueError("dimensión de embedding incompatible con el índice (¿cambió el modelo?)")
        return self.matrix @ q.astype(np.float32)


def _load(session: Session, user_id: int) -> list[ProfileNode]:
    return list(session.exec(select(ProfileNode).where(ProfileNode.user_id == user_id).order_by(ProfileNode.id)).all())


def _to_index(rows: list[ProfileNode]) -> ProfileIndex:
    nodes = [Node(r.id, r.level, r.kind, r.ref, r.text, r.parent_id, np.frombuffer(r.embedding, dtype=np.float32)) for r in rows]
    return ProfileIndex(nodes, rows[0].embed_model if rows else "", rows[0].profile_hash if rows else "")


def ensure_tree(session: Session, user_id: int, profile: Profile, embedder: Embedder) -> ProfileIndex:
    """Devuelve el índice vigente; reconstruye si cambió el perfil o el modelo de embeddings."""
    ph = profile_hash(profile)
    rows = _load(session, user_id)
    if rows and all(r.profile_hash == ph and r.embed_model == embedder.name for r in rows):
        return _to_index(rows)
    with _rebuild_lock:
        session.expire_all()
        rows = _load(session, user_id)
        if rows and all(r.profile_hash == ph and r.embed_model == embedder.name for r in rows):
            return _to_index(rows)
        raw = build_nodes(profile)
        vecs = embedder.embed([n.text for n in raw], "doc")
        model = embedder.name                        # el backend efectivo (auto) se conoce tras embeber
        session.exec(delete(ProfileNode).where(ProfileNode.user_id == user_id))  # type: ignore[arg-type]
        ids: dict[int, int] = {}
        for n, v in zip(raw, vecs, strict=True):                  # padres primero (build_nodes crea la raíz y los MID antes que sus hojas)
            row = ProfileNode(user_id=user_id, level=n.level, kind=n.kind, ref=n.ref, text=n.text,
                              parent_id=ids.get(n.parent) if n.parent is not None else None,
                              embedding=v.astype(np.float32).tobytes(), embed_model=model, profile_hash=ph)
            session.add(row)
            session.flush()
            ids[n.idx] = row.id
        session.commit()
        log.info("árbol del perfil reconstruido: user=%s nodos=%d modelo=%s", user_id, len(raw), model)
        return _to_index(_load(session, user_id))


def invalidate_tree(session: Session, user_id: int) -> None:
    session.exec(delete(ProfileNode).where(ProfileNode.user_id == user_id))  # type: ignore[arg-type]
    session.commit()
