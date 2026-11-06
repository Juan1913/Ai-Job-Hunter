import pytest

from app.ats.gaps import classify
from app.cv.strategies import STRATEGIES, evaluate, tree_of_thought
from app.cv.tailor import _assemble, tailor_cv
from app.domain.job import Job
from app.domain.tailoring import GapStatus
from tests.fakes import PROFILE, FakeLLM

JOB = Job(title="Analista SOC", company="Bancolombia", url="u", source="t",
          description="Requiere SIEM, AWS, respuesta a incidentes y monitoreo de seguridad")
GAPS = classify(["SIEM", "respuesta a incidentes", "AWS"], PROFILE, FakeLLM())
assert {g.keyword: g.status for g in GAPS}["AWS"] == GapStatus.MISSING

GOOD = {"headline": "Analista SOC", "summary": "Analista SOC con 4 años en monitoreo SIEM y respuesta a incidentes.",
        "skills": ["SIEM", "Splunk", "Python"],
        "experience": [{"bullets": ["Monitoreé alertas SIEM en Splunk 24/7 como analista SOC", "Ejecuté respuesta a incidentes de phishing y malware"]}]}


def variant(**kw):
    d = {**GOOD, **kw}
    return d


class ScriptedWriter:
    """Writer simulado: la respuesta depende de la estrategia que aparezca en el prompt y de si es refinamiento."""

    def __init__(self, by_strategy: dict[str, dict | Exception], refined: dict | None = None):
        self.by, self.refined, self.prompts = by_strategy, refined, []

    def complete_json(self, system, user):
        self.prompts.append(user)
        if "BORRADOR ANTERIOR" in user and self.refined is not None:
            return self.refined
        for s in STRATEGIES:
            if f"({s.name})" in user:
                out = self.by.get(s.key, GOOD)
                if isinstance(out, Exception):
                    raise out
                return out
        return GOOD

    @property
    def calls(self):
        return len(self.prompts)


def test_branches_one_degrades_to_legacy_behavior():
    llm = FakeLLM()
    cv = tailor_cv(PROFILE, JOB, GAPS, llm, branches=1, depth=3)
    assert llm.calls == 1 and any(t.startswith("intento 1") for t in cv.trace) and not any("[ToT]" in t for t in cv.trace)
    legacy = tailor_cv(PROFILE, JOB, GAPS, FakeLLM())              # defaults de settings: tot_branches=1
    assert legacy.text == cv.text and legacy.trace == cv.trace


def test_legacy_retries_still_work_with_bad_first():
    cv = tailor_cv(PROFILE, JOB, GAPS, FakeLLM(bad_first=True), branches=1)
    assert "AWS" not in cv.text and len(cv.trace) == 2


def test_tot_depth1_spends_one_call_per_branch_and_traces_each_strategy():
    w = ScriptedWriter({})
    cv = tailor_cv(PROFILE, JOB, GAPS, w, branches=3, depth=1)
    assert w.calls == 3
    names = [s.name for s in STRATEGIES[:3]]
    assert all(any(n in t for t in cv.trace) for n in names) and STRATEGIES[3].name not in " ".join(cv.trace)
    assert cv.trace[-1].startswith("[ToT] elegida") and "llamadas al writer=3" in cv.trace[-1]
    assert cv.ats is not None and cv.ats.keyword_coverage == 1.0


def test_tot_discards_branch_that_leaks_unconfirmed_keyword():
    leaky = variant(summary="Analista SOC con SIEM, respuesta a incidentes y experiencia en AWS y Python Splunk.", skills=["SIEM", "Splunk", "AWS"])
    w = ScriptedWriter({"keywords": leaky})
    cv = tailor_cv(PROFILE, JOB, GAPS, w, branches=2, depth=1)
    assert "AWS" not in cv.text
    line = next(t for t in cv.trace if "denso en keywords" in t and "nivel=1" in t)
    assert "fugas" in line


def test_tot_picks_best_scoring_branch_and_prunes_to_beam():
    stuffed = variant(summary=JOB.description + ". " + JOB.description)           # copia la oferta
    w = ScriptedWriter({"keywords": stuffed, "pivot": stuffed})
    res = tree_of_thought(lambda s, d, i: _assemble(PROFILE, JOB, w.complete_json("", f"({s.name})"), GAPS), PROFILE, JOB, GAPS, branches=4, depth=1)
    statuses = {b.strategy.key: b.status for b in res.branches}
    assert list(statuses.values()).count("podada") == 2                          # 4 ramas, beam 2
    assert statuses["keywords"] == "podada" and statuses["pivot"] == "podada"
    assert sum(1 for b in res.branches if b.status == "elegida") == 1


def test_tot_depth2_refines_best_branches_with_their_issues_and_stays_in_budget():
    incomplete = variant(summary="Analista SOC con SIEM que redujo incidentes un 85% y respuesta a incidentes.")   # cifra inventada
    w = ScriptedWriter({"keywords": incomplete, "achievements": incomplete}, refined=GOOD)
    cv = tailor_cv(PROFILE, JOB, GAPS, w, branches=2, depth=2)
    assert 2 < w.calls <= 2 + 1 * 2                                               # branches + (depth-1)*beam
    refine = [p for p in w.prompts if "BORRADOR ANTERIOR" in p]
    assert refine and all("CORRIGE ESTOS PROBLEMAS" in p and "85%" in p for p in refine)
    assert cv.ats.keyword_coverage == 1.0 and any("nivel=2" in t and "refina" in t for t in cv.trace)


def test_tot_skips_refinement_when_branches_are_already_clean():
    long_bullets = ["Revisé alertas de red cada semana y documenté los hallazgos", "Analicé eventos de autenticación sospechosos e informé a la gerencia",
                    "Clasifiqué correos maliciosos reportados por usuarios finales", "Auditoré accesos privilegiados y propuse controles adicionales",
                    "Prioricé vulnerabilidades críticas junto al equipo de infraestructura", "Afiné reglas de detección para reducir falsos positivos"]
    big = variant(summary=GOOD["summary"] + " Experiencia en monitoreo continuo, análisis de alertas, coordinación con equipos técnicos y reportes para la gerencia.",
                  experience=[{"bullets": long_bullets + ["Ejecuté respuesta a incidentes de phishing y malware con contención y reporte"]}])
    w = ScriptedWriter({"keywords": big, "achievements": big})
    res = tree_of_thought(lambda s, d, i: _assemble(PROFILE, JOB, w.complete_json("", f"({s.name})"), GAPS), PROFILE, JOB, GAPS, branches=2, depth=3)
    assert all(b.ev.clean for b in res.branches), [b.ev for b in res.branches]
    assert res.calls == 2                                                          # nada que refinar: no se gastan llamadas lentas


def test_tot_survives_failing_branches_and_fails_only_if_all_fail():
    w = ScriptedWriter({"keywords": RuntimeError("json roto")})
    cv = tailor_cv(PROFILE, JOB, GAPS, w, branches=2, depth=1)
    assert any("error de generación" in t for t in cv.trace) and cv.ats
    allbad = ScriptedWriter({s.key: RuntimeError("caído") for s in STRATEGIES})
    with pytest.raises(RuntimeError, match="No se pudo generar el CV"):
        tailor_cv(PROFILE, JOB, GAPS, allbad, branches=2, depth=2)


def test_evaluate_penalizes_stuffing_invented_numbers_and_repetition():
    ok = _assemble(PROFILE, JOB, GOOD, GAPS)
    base = evaluate(ok, PROFILE, JOB, GAPS)
    assert not any("copia" in n or "cifras" in n or "stuffing" in n for n, _ in base.penalties)
    long_job = JOB.model_copy(update={"description": "Buscamos un analista SOC para monitorear eventos de seguridad con SIEM, atender alertas, "
                                      "ejecutar respuesta a incidentes de phishing y malware, documentar hallazgos y coordinar con el equipo de infraestructura"})
    copied = evaluate(_assemble(PROFILE, long_job, variant(summary=long_job.description), GAPS), PROFILE, long_job, GAPS)
    assert any("copia de la oferta" in n for n, _ in copied.penalties) and copied.score < evaluate(ok, PROFILE, long_job, GAPS).score
    inv = evaluate(_assemble(PROFILE, JOB, variant(summary="Analista SOC SIEM que redujo incidentes un 85% y atendió 1200 casos."), GAPS), PROFILE, JOB, GAPS)
    assert any("cifras" in n for n, _ in inv.penalties) and any("85%" in i for i in inv.issues)
    rep = evaluate(_assemble(PROFILE, JOB, variant(summary="SIEM SIEM SIEM SIEM SIEM SIEM. Analista SOC con respuesta a incidentes."), GAPS), PROFILE, JOB, GAPS)
    assert any("stuffing" in n for n, _ in rep.penalties)


def test_evaluate_leak_is_heaviest_and_marks_branch_invalid():
    leaky = evaluate(_assemble(PROFILE, JOB, variant(summary="Analista SOC con AWS, SIEM y respuesta a incidentes."), GAPS), PROFILE, JOB, GAPS)
    assert leaky.leaks == 1 and leaky.key[0] is False and any(p >= 30 for _, p in leaky.penalties)


def test_golden_rule_holds_with_confirmed_keyword():
    gaps = classify(["SIEM", "AWS"], PROFILE, FakeLLM(), confirmed={"AWS"})
    out = variant(summary="Analista SOC con SIEM y AWS.", skills=["SIEM", "AWS"])
    ev = evaluate(_assemble(PROFILE, JOB, out, gaps), PROFILE, JOB, gaps)
    assert ev.leaks == 0
