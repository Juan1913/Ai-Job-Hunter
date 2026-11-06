
from app.agents.harness import run_with_guard
from app.ats.checker import check
from app.ats.gaps import classify, match_score
from app.domain.job import Job
from app.domain.tailoring import GapStatus
from tests.fakes import PROFILE, FakeLLM

JOB = Job(title="Analista SOC", company="Bancolombia", url="u", source="t", description="Requiere SIEM, AWS, respuesta a incidentes")


def test_classify_have_adjacent_missing_and_rejects_fake_evidence():
    gaps = {g.keyword: g for g in classify(["SIEM", "AWS", "respuesta a incidentes", "Kubernetes"], PROFILE, FakeLLM())}
    assert gaps["SIEM"].status == GapStatus.HAVE
    assert gaps["respuesta a incidentes"].status == GapStatus.ADJACENT
    assert gaps["AWS"].status == GapStatus.MISSING
    assert gaps["Kubernetes"].status == GapStatus.MISSING   # evidencia inventada -> rechazada
    assert match_score(list(gaps.values())) == round(100 * (1 + 0.6) / 4)


def test_checker_flags_unconfirmed_keyword_leak():
    gaps = classify(["SIEM", "AWS"], PROFILE, FakeLLM())
    r = check("PROFESSIONAL SUMMARY SKILLS WORK EXPERIENCE EDUCATION luis@x.com SIEM AWS " + "palabra " * 160, gaps)
    assert any("AWS" in i and "no lo ha confirmado" in i for i in r.issues)


def test_harness_retries_with_feedback_and_keeps_best():
    seen = []
    res = run_with_guard(lambda iss: (seen.append(list(iss)) or len(seen)), lambda v: [] if v == 3 else ["mal"], 3)
    assert res.ok and res.value == 3 and seen[1] == ["mal"]


def test_clean_title_and_fuzzy_coverage():
    from app.text import clean_title, covered, norm
    assert clean_title("Agente de SOC 7 *24 ( Analista de ciberseguridad))") == "Agente de SOC"
    assert clean_title("Analista SOC") == "Analista SOC"
    assert covered(norm("Conozco MITRE ATT&CK y Splunk"), "Framework MITRE ATT&CK")
    assert not covered(norm("Tengo AWS"), "Azure Sentinel")
    assert not covered(norm("laws"), "AWS")
