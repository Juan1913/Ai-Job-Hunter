"""Punto de entrada de alto nivel: recuperador del perfil de un usuario, tolerante a fallos de embeddings."""
from __future__ import annotations

import logging

from sqlmodel import Session

from app.domain.profile import Profile
from app.rag.embeddings import Embedder, EmbeddingError, get_embedder
from app.rag.retrieve import ProfileRetriever
from app.rag.store import ensure_tree

log = logging.getLogger(__name__)


def retriever_for(session: Session, user_id: int, profile: Profile, embedder: Embedder | None = None) -> ProfileRetriever | None:
    """Árbol vigente del usuario (se reconstruye solo si cambió el perfil). None si no se pudo -> el llamador degrada
    al comportamiento sin RAG en vez de fallar."""
    embedder = embedder or get_embedder()
    try:
        return ProfileRetriever(ensure_tree(session, user_id, profile, embedder), embedder)
    except (EmbeddingError, ValueError) as e:
        log.warning("RAG del perfil no disponible (user=%s): %s", user_id, e)
        return None
