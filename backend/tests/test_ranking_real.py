"""Regresión del ranking contra 36 ofertas REALES (Colombia, 2026-11-04) etiquetadas para un perfil de analista SOC.
Etiquetas puestas a mano por el autor del proyecto: es una muestra pequeña y de un solo perfil, así que protege contra
regresiones, no demuestra calidad general. Embeddings por hashing (determinista en CI); con nomic real las cifras son mejores:
AUC 0.98, 8 % de ofertas ajenas sin descartar (ver scripts/calibrate_ranking.py)."""
import pytest
from sqlmodel import Session

from app.db import User, engine
from app.matcher import calibration as cal

FIXTURE = "tests/fixtures_rank/soc_real.json"


@pytest.fixture
def scored():
    profile, rows = cal.load(FIXTURE)
    with Session(engine) as s:
        u = User(email="cal@x.com", password_hash="x")
        s.add(u)
        s.commit()
        s.refresh(u)
        return cal.score_all(profile, rows, s, u.id)


def test_evaluation_separates_relevant_from_irrelevant(scored):
    assert cal.auc(scored, "score") >= 0.92                                  # antes de la puerta de relevancia: 0.77
    for half in (0, 1):                                                       # ambas mitades: detecta sobreajuste a una sola
        assert cal.auc([s for s in scored if s.id % 2 == half], "score") >= 0.88


def test_irrelevant_offers_are_skipped_and_relevant_ones_are_not(scored):
    q = cal.rec_quality(scored)
    assert q["irrelevant_not_skipped"] <= 0.10                                # antes: 32 % (hash) / 40-72 % (nomic)
    assert q["relevant_skipped"] <= 0.20
    assert q["apply_precision"] >= 0.9


def test_evaluation_does_not_make_the_ranking_worse_than_stage1(scored):
    assert cal.auc(scored, "score") >= cal.auc(scored, "stage1") - 0.02


def test_generic_title_words_do_not_count_as_role_match():
    from app.matcher.hybrid import _role_tokens_overlap
    assert _role_tokens_overlap("analista soc", "Analista de Datos") == 0.0
    assert _role_tokens_overlap("analista soc", "SOC Analyst L2") == 1.0
    assert _role_tokens_overlap("analista", "Analista Senior") > 0        # rol solo genérico: cae al solapamiento completo


def test_target_role_synonyms_recognize_cybersecurity_titles():
    from app.matcher.hybrid import _with_synonyms
    roles = [r.lower() for r in _with_synonyms(["analista soc"])]
    assert "analista soc" in roles and any("ciberseguridad" in r or "security" in r for r in roles)
