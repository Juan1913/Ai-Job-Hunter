"""Embeddings: cliente OpenAI-compatible (/v1/embeddings, p. ej. Ollama) + fallback determinista offline.

Diseño:
- `OllamaEmbedder`: modelo real (nomic-embed-text, bge-m3...). Cliente httpx propio (no usa app/llm/client.py).
- `HashEmbedder`: hashing de n-gramas (palabras, bigramas y n-gramas de caracteres) con signo. Sin modelo, sin red,
  determinista entre procesos (blake2b, no `hash()`). Capta similitud léxica/morfológica, NO semántica profunda.
- `ResilientEmbedder`: modo "auto": usa el modelo y, si el servidor no responde, cae a hashing durante un rato.
- `CachedEmbedder`: caché persistente en SQLite por hash(modelo + prefijo + texto).
Todos devuelven matrices float32 (n, d) con filas normalizadas L2, así coseno = producto punto.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Protocol

import httpx
import numpy as np
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.config import settings
from app.text import norm

log = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    pass


class Embedder(Protocol):
    name: str
    sim_lo: float      # coseno que equivale a "nada que ver" (para normalizar a 0..1)
    sim_hi: float      # coseno que equivale a "muy parecido"

    def embed(self, texts: list[str], kind: str = "doc") -> np.ndarray: ...


def _unit(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return (m / n).astype(np.float32)


def scale_sim(embedder: Embedder, cos: float) -> float:
    """Coseno -> 0..1 según el rango calibrado de cada modelo (los cosenos de cada modelo viven en rangos distintos)."""
    return float(min(max((cos - embedder.sim_lo) / max(embedder.sim_hi - embedder.sim_lo, 1e-6), 0.0), 1.0))


_STOP = {
    "de", "la", "el", "en", "y", "a", "los", "las", "con", "para", "por", "un", "una", "del", "al", "se", "que", "o", "e",
    "the", "and", "of", "to", "in", "for", "or", "with", "an", "is", "are", "on", "as", "at", "be", "by",
}
_WORD = re.compile(r"[a-z0-9+#&]+")


class HashEmbedder:
    """Hashing trick con n-gramas. 512 dimensiones: suficiente para ~cientos de textos cortos por usuario."""

    sim_lo, sim_hi = 0.04, 0.40

    def __init__(self, dim: int = 512):
        self.dim = dim
        self.name = f"hash-v1-{dim}"

    def _features(self, text: str) -> dict[str, float]:
        words = [w for w in _WORD.findall(norm(text)) if w not in _STOP]
        feats: dict[str, float] = {}

        def add(k: str, w: float) -> None:
            feats[k] = feats.get(k, 0.0) + w

        for w in words:
            add("w:" + w, 1.0)
            padded = f"^{w}$"
            if len(w) >= 4:                      # n-gramas de caracteres: "incidentes" ~ "incidents", "monitoreo" ~ "monitoring"
                for n in (4, 5):
                    for i in range(len(padded) - n + 1):
                        add(f"c{n}:" + padded[i:i + n], 0.35)
        for a, b in zip(words, words[1:], strict=False):
            add(f"b:{a} {b}", 0.6)
        return feats

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for k, w in self._features(text).items():
            h = int.from_bytes(hashlib.blake2b(k.encode(), digest_size=8).digest(), "little")
            v[h % self.dim] += (1.0 if (h >> 63) & 1 else -1.0) * (1.0 + np.log(w) if w > 1 else w)
        return v

    def embed(self, texts: list[str], kind: str = "doc") -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return _unit(np.stack([self._vec(t) for t in texts]))


# Rangos de coseno típicos (calibrados a ojo con pares relacionados/no relacionados; ver docs del matcher).
_MODEL_RANGES = {"nomic": (0.40, 0.80), "bge": (0.35, 0.80)}
# nomic-embed-text mejora con prefijos de tarea.
_PREFIX = {"nomic": {"query": "search_query: ", "doc": "search_document: "}}


class OllamaEmbedder:
    def __init__(self, model: str | None = None, base_url: str | None = None, timeout_s: float | None = None):
        self.model = model or settings.llm_model_embed
        root = (base_url or settings.embed_base_url or settings.llm_base_url).rstrip("/")
        self.base_url = root if root.endswith("/v1") else root + "/v1"   # el chat nativo usa la raíz; /embeddings vive bajo /v1
        self.timeout_s = timeout_s or settings.embed_timeout_s
        self.name = f"ollama:{self.model}"
        fam = next((k for k in _MODEL_RANGES if k in self.model.lower()), None)
        self.sim_lo, self.sim_hi = _MODEL_RANGES.get(fam, (0.30, 0.75))
        self._prefix = _PREFIX.get(fam, {})

    def embed(self, texts: list[str], kind: str = "doc") -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        pre = self._prefix.get(kind, "")
        out: list[list[float]] = []
        try:
            for i in range(0, len(texts), max(settings.embed_batch, 1)):
                batch = [pre + t[:6000] for t in texts[i:i + settings.embed_batch]]
                r = httpx.post(f"{self.base_url}/embeddings", json={"model": self.model, "input": batch}, timeout=self.timeout_s)
                r.raise_for_status()
                data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
                if len(data) != len(batch):
                    raise EmbeddingError("el servidor devolvió un número distinto de embeddings")
                out += [d["embedding"] for d in data]
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as e:
            raise EmbeddingError(f"Embeddings no disponibles: {e}") from e
        return _unit(np.array(out, dtype=np.float32))


class ResilientEmbedder:
    """Modo auto: modelo real si responde; si no, hashing durante `retry_after_s` (evita esperar timeouts en cada llamada)."""

    def __init__(self, primary: Embedder, fallback: Embedder, retry_after_s: float = 60.0):
        self.primary, self.fallback, self.retry_after_s = primary, fallback, retry_after_s
        self._down_until = 0.0
        self.name = primary.name
        self.sim_lo, self.sim_hi = primary.sim_lo, primary.sim_hi

    def _use(self, e: Embedder) -> None:
        self.name, self.sim_lo, self.sim_hi = e.name, e.sim_lo, e.sim_hi

    def embed(self, texts: list[str], kind: str = "doc") -> np.ndarray:
        if time.monotonic() >= self._down_until:
            try:
                m = self.primary.embed(texts, kind)
                self._use(self.primary)
                return m
            except EmbeddingError as e:
                log.warning("embeddings: %s; usando hashing %.0fs", e, self.retry_after_s)
                self._down_until = time.monotonic() + self.retry_after_s
        self._use(self.fallback)
        return self.fallback.embed(texts, kind)


def _key(model: str, kind: str, text: str) -> str:
    return hashlib.sha256(f"{model}\x00{kind}\x00{text}".encode()).hexdigest()


class CachedEmbedder:
    """Caché persistente por hash de texto. El modelo efectivo se lee de `inner.name` tras cada cálculo."""

    def __init__(self, inner: Embedder, engine=None):
        self.inner = inner
        self._engine = engine

    @property
    def engine(self):
        if self._engine is None:
            from app.db import engine
            return engine
        return self._engine

    name = property(lambda self: self.inner.name)
    sim_lo = property(lambda self: self.inner.sim_lo)
    sim_hi = property(lambda self: self.inner.sim_hi)

    def embed(self, texts: list[str], kind: str = "doc") -> np.ndarray:
        from app.db import EmbeddingCache
        if not texts:
            return self.inner.embed([], kind)
        model = self.inner.name
        keys = [_key(model, kind, t) for t in texts]
        with Session(self.engine) as s:
            hit = {c.key: np.frombuffer(c.vector, dtype=np.float32) for c in s.exec(select(EmbeddingCache).where(EmbeddingCache.key.in_(set(keys)))).all()}
        missing = [i for i, k in enumerate(keys) if k not in hit]
        todo: dict[str, str] = {}
        for i in missing:
            todo.setdefault(keys[i], texts[i])
        if todo:
            vecs = self.inner.embed(list(todo.values()), kind)
            real = self.inner.name                      # el backend pudo cambiar (auto): re-clavea con el modelo efectivo
            with Session(self.engine) as s:
                for t, v in zip(todo.values(), vecs, strict=True):
                    k2 = _key(real, kind, t)
                    hit[k2] = v
                    if s.get(EmbeddingCache, k2) is None:
                        try:
                            with s.begin_nested():       # savepoint: otra búsqueda pudo insertar la misma clave entre el get y el add
                                s.add(EmbeddingCache(key=k2, model=real, vector=v.astype(np.float32).tobytes()))
                        except IntegrityError:
                            pass                          # ya estaba: el vector es determinista por (modelo, tipo, texto)
                s.commit()
            keys = [_key(real, kind, t) for t in texts]
        rows = [hit[k] for k in keys]
        if len({r.shape for r in rows}) > 1:            # mezcla de backends (auto cayó a hashing a mitad): recalcula todo coherente
            return self.inner.embed(texts, kind)
        return np.stack(rows).astype(np.float32)


_singleton: Embedder | None = None


def build_embedder(backend: str | None = None) -> Embedder:
    backend = (backend or settings.embed_backend).lower()
    from app.ai.mode import ai_mode
    if backend == "hash" or ai_mode() == "none":       # modo none: ni siquiera se intenta contactar un servidor de modelos
        return CachedEmbedder(HashEmbedder())
    if backend == "ollama":
        return CachedEmbedder(OllamaEmbedder())
    return CachedEmbedder(ResilientEmbedder(OllamaEmbedder(), HashEmbedder()))


def get_embedder() -> Embedder:
    global _singleton
    if _singleton is None:
        _singleton = build_embedder()
    return _singleton


def reset_embedder() -> None:
    global _singleton
    _singleton = None
