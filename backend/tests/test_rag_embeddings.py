import httpx
import numpy as np
import pytest
from sqlmodel import Session, func, select

from app.db import EmbeddingCache, engine
from app.rag.embeddings import CachedEmbedder, EmbeddingError, HashEmbedder, OllamaEmbedder, ResilientEmbedder, build_embedder, scale_sim


def test_hash_embedder_is_deterministic_normalized_and_ranks_related_text_higher():
    e = HashEmbedder()
    a = e.embed(["Respondí a incidentes de phishing y malware"])
    assert np.allclose(a, HashEmbedder().embed(["Respondí a incidentes de phishing y malware"]))
    assert abs(float(np.linalg.norm(a[0])) - 1.0) < 1e-5
    q, rel, unrel = e.embed(["respuesta a incidentes de seguridad", "Respondí a incidentes de phishing", "Cocinero de restaurante italiano"])
    assert q @ rel > q @ unrel + 0.1
    assert e.embed([]).shape == (0, e.dim)
    assert not np.any(e.embed([""]))                      # texto vacío -> vector nulo, sin NaN


def test_hash_embedder_crosses_language_by_morphology():
    e = HashEmbedder()
    es, en, other = e.embed(["monitoreo de incidentes", "incident monitoring", "pastelería artesanal"])
    assert es @ en > es @ other


def test_scale_sim_clamps_to_unit_range():
    e = HashEmbedder()
    assert scale_sim(e, -1) == 0.0 and scale_sim(e, 1) == 1.0
    assert 0 < scale_sim(e, (e.sim_lo + e.sim_hi) / 2) < 1


def _fake_post(calls, dim=4):
    def post(url, json=None, timeout=None):
        calls.append((url, json))
        data = [{"index": i, "embedding": [float(i + 1)] + [0.0] * (dim - 1)} for i in range(len(json["input"]))][::-1]  # desordenado a propósito
        return httpx.Response(200, json={"data": data}, request=httpx.Request("POST", url))
    return post


def test_ollama_embedder_speaks_openai_embeddings_api(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "post", _fake_post(calls))
    e = OllamaEmbedder(model="nomic-embed-text", base_url="http://x/v1/")
    m = e.embed(["uno", "dos"], kind="query")
    url, body = calls[0]
    assert url == "http://x/v1/embeddings" and body["model"] == "nomic-embed-text"
    assert body["input"][0].startswith("search_query: ")      # prefijo de tarea de nomic
    assert m.shape == (2, 4) and np.allclose(np.linalg.norm(m, axis=1), 1)
    assert e.name == "ollama:nomic-embed-text"


def test_ollama_embedder_wraps_http_errors(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("sin servidor")
    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(EmbeddingError):
        OllamaEmbedder(base_url="http://x/v1").embed(["a"])


class Down:
    name, sim_lo, sim_hi = "down", 0.0, 1.0

    def embed(self, texts, kind="doc"):
        raise EmbeddingError("caído")


def test_resilient_falls_back_to_hash_and_remembers_outage():
    r = ResilientEmbedder(Down(), HashEmbedder())
    m = r.embed(["hola mundo"])
    assert m.shape[1] == 512 and r.name == "hash-v1-512"
    r.primary = None                                      # si volviera a consultarse el primario explotaría
    assert r.embed(["otra vez"]).shape[1] == 512


class Counting(HashEmbedder):
    def __init__(self):
        super().__init__(64)
        self.seen = []

    def embed(self, texts, kind="doc"):
        self.seen += list(texts)
        return super().embed(texts, kind)


def test_cache_embeds_each_text_once_and_persists():
    inner = Counting()
    c = CachedEmbedder(inner, engine)
    a = c.embed(["alfa", "beta", "alfa"])
    b = c.embed(["beta", "gamma"])
    assert inner.seen == ["alfa", "beta", "gamma"]        # duplicados y repetidos no se recalculan
    assert np.allclose(a[1], b[0])
    with Session(engine) as s:
        assert s.exec(select(func.count()).select_from(EmbeddingCache)).one() == 3
    assert CachedEmbedder(Counting(), engine).embed(["alfa"]).shape == (1, 64)


def test_build_embedder_hash_backend_is_cached_hash():
    e = build_embedder("hash")
    assert e.name.startswith("hash-v1") and e.embed(["x y z"]).shape[0] == 1
