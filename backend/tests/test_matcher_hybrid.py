import pytest
from sqlmodel import Session

from app.db import User, engine
from app.domain.job import Job
from app.domain.profile import Profile, SearchPreferences
from app.matcher.evalmetrics import ndcg_at_k, recall_at_k
from app.matcher.filters import countries_in, detect_language, hard_filters
from app.matcher.hybrid import FILTERED_CAP, Stage1Scorer, stage1_hybrid
from app.matcher.lexical import stage1_score
from app.matcher.service import analyze_job, build_scorer, pick_for_stage2
from app.rag.embeddings import CachedEmbedder, EmbeddingError, HashEmbedder
from app.rag.retrieve import ProfileRetriever
from app.rag.store import ensure_tree
from tests.fakes import PROFILE, FakeLLM
from tests.matcher_eval_data import DATASET, SOC, SOC_JOBS, J

EMB = CachedEmbedder(HashEmbedder(), engine)


def _retriever(session, uid, profile):
    return ProfileRetriever(ensure_tree(session, uid, profile, EMB), EMB)


@pytest.fixture
def session():
    with Session(engine) as s:
        for i, (name, _, _) in enumerate(DATASET, 1):
            s.add(User(id=i, email=f"{name}@x.com", password_hash="x"))
        s.commit()
        yield s


def test_metrics():
    assert ndcg_at_k([2, 1, 0], 3) == pytest.approx(1.0)
    assert ndcg_at_k([0, 1, 2], 3) < 0.7
    assert recall_at_k([2, 0, 2, 0], 1) == 0.5 and recall_at_k([0, 0], 2) == 1.0


def test_filters_country_remote_language():
    p = SearchPreferences(countries=["CO"], languages=["es"], remote=False)
    ok = J("Analista", "Buscamos analista con experiencia en seguridad para trabajar con el equipo de la empresa en Bogotá.", "Bogotá, Colombia")
    assert hard_filters(ok, p) == []
    assert any("país" in r for r in hard_filters(J("A", "x", "Madrid, España"), p))
    assert any("remot" in r for r in hard_filters(J("A", "x", "Bogotá", remote=True), p))
    en = J("Analyst", "We are looking for an analyst with experience in security to join our team and work with the engineers.", "Bogotá, Colombia")
    assert any("idioma" in r for r in hard_filters(en, p))
    # remoto permitido: la ubicación no filtra; ubicación desconocida o sin preferencia de país: pasa
    p2 = SearchPreferences(countries=["CO"], remote=True)
    assert hard_filters(J("A", "x", "Madrid, España", remote=True), p2) == []
    assert hard_filters(J("A", "x", ""), p2) == [] and hard_filters(J("A", "x", "Madrid"), SearchPreferences()) == []


def test_country_and_language_detection():
    assert countries_in("Ciudad de México") == {"MX"} and countries_in("Remote") == set()
    assert "US" in countries_in("Austin, United States")
    assert detect_language("Buscamos una persona con experiencia para el equipo de la empresa") == "es"
    assert detect_language("We are looking for a person with experience to join our team") == "en"
    assert detect_language("SIEM Splunk") is None


def test_filtered_jobs_sink_but_keep_their_reasons(session):
    sc = Stage1Scorer(SOC, _retriever(session, 1, SOC))
    madrid = next(j for j, _ in SOC_JOBS if "Madrid" in j.location)
    r = sc.score(madrid)
    assert r.filtered and r.score <= FILTERED_CAP and any("país" in x for x in r.reasons)
    bogota = sc.score(SOC_JOBS[0][0])
    assert not bogota.filtered and bogota.score > 50 and bogota.semantic and bogota.role


def test_far_title_from_target_roles_is_penalized(session):
    sc = Stage1Scorer(SOC, _retriever(session, 1, SOC))
    r = sc.score(J("Chef Ejecutivo", "Lideraremos la cocina de nuestro restaurante con menús y control de costos para el equipo.", "Bogotá, Colombia"))
    assert any("roles objetivo" in x for x in r.reasons) and r.score < 25


def test_degrades_to_lexical_when_embeddings_fail():
    class Broken(HashEmbedder):
        def embed(self, texts, kind="doc"):
            raise EmbeddingError("x")

    class Fake:                       # retriever mínimo cuyo embedder falla al embeber ofertas
        embedder = Broken()

    sc = Stage1Scorer(PROFILE, Fake())   # type: ignore[arg-type]
    job = Job(title="Analista SOC", company="B", url="u", source="t", description="Requiere SIEM y Splunk")
    sc.prefetch([job])
    r = sc.score(job)
    assert r.semantic is None and r.score == stage1_score(PROFILE, job)
    assert stage1_hybrid(PROFILE, job).score == r.score          # sin retriever: igual


def test_build_scorer_and_runner_style_batch(session):
    sc = build_scorer(session, 1, SOC, ["Analista SOC"])
    jobs = [j for j, _ in SOC_JOBS]
    sc.prefetch(jobs)
    scores = [sc.score(j).score for j in jobs]
    assert max(scores) == scores[0] or max(scores) in scores[:3]
    assert all(0 <= s <= 100 for s in scores)


def test_pick_for_stage2_topk_and_threshold():
    assert pick_for_stage2([(1, 80), (2, 10), (3, 60), (4, 70)], k=2) == [1, 4]
    assert pick_for_stage2([(1, 20)], k=5, min_stage1=25) == []


def test_analyze_job_stage2_with_fake_llm(session):
    r = _retriever(session, 1, PROFILE)
    job = Job(title="Analista SOC", company="B", url="u", source="t", description="SIEM AWS respuesta a incidentes")
    gaps, score = analyze_job(job, PROFILE, FakeLLM(), retriever=r)
    assert score == round(100 * (1 + 0.6 + 0.6) / 5)   # SIEM have; cargo y "respuesta a incidentes" adyacentes; AWS y Kubernetes missing
    assert {g.keyword for g in gaps} >= {"SIEM", "AWS"}


# ---- Evaluación de ranking (dataset ES/EN) -----------------------------------------------------------------

def _rank(session, uid, profile, jobs, mode):
    if mode == "lexical":
        scored = [(stage1_score(profile, j, profile.preferences.target_roles), rel) for j, rel in jobs]
    else:
        sc = Stage1Scorer(profile, _retriever(session, uid, profile))
        sc.prefetch([j for j, _ in jobs])
        scored = [(sc.score(j).score, rel) for j, rel in jobs]
    return [rel for _, rel in sorted(scored, key=lambda t: -t[0])]


def test_hybrid_ranking_quality_on_eval_dataset(session):
    per = {}
    for uid, (name, prof, jobs) in enumerate(DATASET, 1):
        rels = _rank(session, uid, prof, jobs, "hybrid")
        per[name] = (ndcg_at_k(rels, 5), recall_at_k(rels, 3))
    mean_ndcg = sum(v[0] for v in per.values()) / len(per)
    mean_rec = sum(v[1] for v in per.values()) / len(per)
    assert mean_ndcg >= 0.93, per        # medido: ~0.96 con hashing, ~0.98 con nomic-embed-text
    assert mean_rec >= 0.9, per          # los relevantes (2) están en el top-3
    assert all(v[0] >= 0.88 for v in per.values()), per


def test_hybrid_beats_lexical_baseline(session):
    h = l_ = 0.0
    for uid, (_, prof, jobs) in enumerate(DATASET, 1):
        h += ndcg_at_k(_rank(session, uid, prof, jobs, "hybrid"), 5)
        l_ += ndcg_at_k(_rank(session, uid, prof, jobs, "lexical"), 5)
    assert h > l_


def test_irrelevant_never_outranks_relevant_in_each_profile(session):
    for uid, (name, prof, jobs) in enumerate(DATASET, 1):
        rels = _rank(session, uid, prof, jobs, "hybrid")
        assert 0 not in rels[:3], (name, rels)          # ningún irrelevante en el top-3


def test_profile_without_skills_does_not_crash(session):
    p = Profile(headline="Chef")
    r = Stage1Scorer(p, _retriever(session, 1, p)).score(J("Chef", "Cocina de restaurante con menús y equipo de cocina para la empresa."))
    assert 0 <= r.score <= 100
