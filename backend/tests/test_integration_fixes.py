"""Regresiones encontradas en la prueba real de punta a punta (2026-10-07)."""
from app.ats.gaps import classify, is_named_term
from app.domain.job import Job
from app.domain.tailoring import GapStatus
from app.rag.embeddings import OllamaEmbedder
from tests.fakes import PROFILE


def _job(source, company, location, title="Agente de SOC 7 *24 ( Analista de ciberseguridad))"):
    return Job(title=title, company=company, location=location, url=f"https://{source}/1", source=source)


def test_same_job_on_two_portals_has_same_dedup_key():
    indeed = _job("indeed", "Unisys", "Bogotá, Cundinamarca")
    linkedin = _job("linkedin", "Unisys", "Bogota, D.C., Capital District, Colombia")
    assert indeed.dedup_key == linkedin.dedup_key
    assert _job("a", "ITM Consulting de Colombia S.A.S", "Bogotá").dedup_key == _job("b", "ITM CONSULTING", "Bogotá D.C.").dedup_key


def test_different_city_or_company_are_different_jobs():
    base = _job("a", "Ara", "Cali", "Asesor comercial")
    assert base.dedup_key != _job("a", "Ara", "Bogotá", "Asesor comercial").dedup_key
    assert base.dedup_key != _job("a", "D1", "Cali", "Asesor comercial").dedup_key


def test_named_products_can_never_be_adjacent():
    assert all(is_named_term(k) for k in ["Darktrace", "CEH", "SC-200", "CySA+", "Kubernetes"])
    assert not any(is_named_term(k) for k in ["respuesta a incidentes", "EDR/XDR", "seguridad en redes"])

    class Llm:   # el modelo pequeño es generoso: da Darktrace como 'parecido' citando evidencia real
        def complete_json(self, system, user):
            return {"adjacent": [{"keyword": "Darktrace", "evidence": "Respondí a incidentes de phishing y malware"},
                                 {"keyword": "respuesta a incidentes", "evidence": "Respondí a incidentes de phishing y malware"}]}

    gaps = {g.keyword: g.status for g in classify(["Darktrace", "respuesta a incidentes"], PROFILE, Llm())}
    assert gaps == {"Darktrace": GapStatus.MISSING, "respuesta a incidentes": GapStatus.ADJACENT}


def test_embeddings_url_works_with_or_without_v1():
    assert OllamaEmbedder(base_url="http://localhost:11434").base_url == "http://localhost:11434/v1"
    assert OllamaEmbedder(base_url="http://localhost:11434/v1").base_url == "http://localhost:11434/v1"


def test_put_preferences_merges_and_keeps_deal_breakers(client, ready):
    from tests.shared import API
    r = client.put(f"{API}/me/preferences", json={"deal_breakers": {"min_salary": 5000000, "excluded_companies": ["Hotel"]}}, headers=ready)
    assert r.json()["preferences"]["deal_breakers"]["min_salary"] == 5000000
    r = client.put(f"{API}/me/preferences", json={"countries": ["MX"]}, headers=ready).json()["preferences"]   # no envía deal_breakers
    assert r["countries"] == ["MX"] and r["target_roles"] == ["analista soc"]
    assert r["deal_breakers"]["min_salary"] == 5000000 and r["deal_breakers"]["excluded_companies"] == ["Hotel"]


def test_embedding_cache_survives_a_concurrent_insert_of_the_same_key():
    """Dos búsquedas de usuarios distintos sobre las mismas ofertas insertan la misma clave: no debe lanzar IntegrityError."""
    import numpy as np
    from sqlmodel import Session

    from app.db import EmbeddingCache, engine
    from app.rag.embeddings import CachedEmbedder, HashEmbedder, _key

    class Racing(HashEmbedder):
        """Mientras calcula, 'otro proceso' ya insertó los mismos vectores."""
        def embed(self, texts, kind="passage"):
            out = super().embed(texts, kind)
            with Session(engine) as s:
                for t, v in zip(texts, out, strict=True):
                    s.add(EmbeddingCache(key=_key(self.name, kind, t), model=self.name, vector=np.asarray(v, dtype=np.float32).tobytes()))
                s.commit()
            return out

    vecs = CachedEmbedder(Racing()).embed(["analista soc en bogota", "contador publico"], "passage")
    assert vecs.shape[0] == 2


def test_worker_builds_per_user_llm_clients(monkeypatch):
    """get_llm() depende del usuario autenticado; el trabajador no puede llamarlo sin argumentos."""
    import inspect

    from app.scheduling import automation
    from app.worker import handlers

    src = inspect.getsource(handlers._task_run) + inspect.getsource(automation.queue_tailors)
    assert "build_llm(" in src and "get_llm(" not in src and "get_writer(" not in src
    assert automation.extractive_tailor_exists() is True      # el modo extractivo de app/ai/writer.py se detecta


def test_query_expansion_llm_respects_ai_mode(monkeypatch):
    from app.config import settings
    from app.scout import query

    monkeypatch.setattr(settings, "ai_mode", "none")
    assert query.expand_query(query.SearchQuery(keywords=["analista soc"]), use_llm=True).variants   # sigue funcionando con el diccionario


def test_real_profile_skills_are_never_dropped_by_the_writer():
    """Con modelos pequeños, el redactor devolvía 2 de 8 habilidades: las reales del perfil se conservan siempre."""
    from app.cv.tailor import _assemble
    from app.domain.tailoring import KeywordGap
    job = Job(title="Analista SOC", company="X", url="u", source="t", description="SIEM")
    gaps = [KeywordGap(keyword="SIEM", status=GapStatus.HAVE)]
    cv = _assemble(PROFILE, job, {"skills": ["SIEM"], "headline": "Analista SOC", "summary": "s"}, gaps)
    assert {s.name for s in PROFILE.skills} <= set(cv.skills)
    assert cv.skills[0] == "SIEM"                                   # lo que pidió el modelo/oferta va primero
    fake = _assemble(PROFILE, job, {"skills": ["SIEM", "Docker", "Terraform"]}, gaps)
    assert "Docker" not in fake.skills and "Terraform" not in fake.skills   # lo inventado sigue fuera
