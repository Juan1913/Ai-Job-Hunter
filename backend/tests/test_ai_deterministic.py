"""Ruta SIN IA: extractor de requisitos, clasificación de brechas, CV extractivo y calidad frente a un LLM simulado.

La calidad se MIDE en 5 profesiones (ventas, contabilidad, enfermería, desarrollo, seguridad) con los CVs de tests/fixtures_cv:
  - recall/precisión del extractor sobre ofertas que NO se usaron para ajustarlo (held-out) y con el léxico apagado,
  - cobertura y score ATS del CV `none` frente al CV de un LLM simulado, ambos medidos contra los mismos requisitos de referencia."""
import re

import pytest
from sqlmodel import Session

from app.ai.requirements import extract_requirements_det
from app.ai.writer import build_summary, select_bullets, tailor_extractive
from app.ats.checker import check
from app.ats.gaps import classify
from app.cv.finalize import finalize_cv
from app.cv.tailor import tailor_cv
from app.db import User, engine
from app.domain.job import Job
from app.domain.profile import Education, Experience, Profile, Skill
from app.domain.tailoring import GapStatus, KeywordGap, includable
from app.rag.service import retriever_for
from app.text import contains, norm
from tests.cv_fakes import ScriptedLLM
from tests.test_cv_evals import CASES, CheatingWriter, _profile

HELD_OUT = {
    "ventas": (Job(title="Ejecutivo de Ventas Corporativas", company="X", url="u", source="t", description=(
        "Somos una empresa líder en soluciones cloud. Requisitos:\n- Experiencia en venta consultiva B2B\n- Manejo de CRM (Salesforce o Pipedrive)\n"
        "- Prospección y cierre de ventas, LinkedIn Sales Navigator\n- Buenas habilidades de comunicación y trabajo en equipo\n"
        "Ofrecemos:\n- Salario competitivo y seguro médico\n- Horario flexible")),
               ["venta consultiva", "Salesforce", "Pipedrive", "Prospección", "LinkedIn Sales Navigator", "cierre de ventas"], ["comunicación", "trabajo en equipo", "seguro médico"]),
    "contabilidad": (Job(title="Analista Contable", company="X", url="u", source="t", description=(
        "Buscamos analista contable con conocimientos de NIIF, conciliaciones bancarias y cierre contable mensual. Manejo de Siigo y Excel avanzado. "
        "Se valora certificación CPA y experiencia en auditoría. Ofrecemos contrato indefinido.")),
                     ["NIIF", "conciliaciones bancarias", "Siigo", "Excel avanzado", "CPA", "auditoría"], ["contrato indefinido", "ofrecemos"]),
    "enfermeria": (Job(title="Enfermera de Urgencias", company="X", url="u", source="t", description=(
        "Hospital busca enfermera para urgencias. Requisitos: triage, RCP avanzada, administración de medicación, curación de heridas y "
        "canalización de vías. Se valora experiencia en pediatría. Turnos rotativos.")),
                   ["triage", "RCP avanzada", "administración de medicación", "curación de heridas", "pediatría"], ["turnos rotativos", "hospital"]),
    "desarrollo": (Job(title="Platform Engineer", company="X", url="u", source="t", description=(
        "You will build our internal platform. Requirements: Go or Python, Docker, Kubernetes, Terraform and GitHub Actions. Experience with "
        "Prometheus and Grafana is a plus. We offer remote work and stock options.")),
                   ["Go", "Python", "Docker", "Kubernetes", "Terraform", "GitHub Actions", "Prometheus", "Grafana"], ["remote work", "stock options"]),
    "seguridad": (Job(title="Security Analyst", company="X", url="u", source="t", description=(
        "Join our SOC. You have hands-on experience with SIEM (Splunk or QRadar), incident response, phishing analysis and EDR tools such as "
        "CrowdStrike. CISSP or Security+ preferred. Knowledge of NIST and ISO 27001.")),
                  ["SIEM", "Splunk", "QRadar", "incident response", "CrowdStrike", "CISSP", "Security+", "NIST", "ISO 27001"], ["hands-on", "preferred"]),
}


def _hits(got: list[str], gold: list[str]) -> list[str]:
    g = {norm(x) for x in got}
    return [x for x in gold if norm(x) in g or any(contains(norm(y), x) for y in got)]


@pytest.mark.parametrize("use_lexicon", [True, False])
def test_requirement_extractor_recall_precision_on_held_out_offers(use_lexicon):
    rec_all, junk = [], []
    for job, gold, noise in HELD_OUT.values():
        got = extract_requirements_det(job, use_lexicon=use_lexicon)
        assert got[0] == job.title and len(got) <= 21                                # cargo primero y con tope
        rec_all.append(len(_hits(got, gold)) / len(gold))
        for n in noise:                                                              # habilidades blandas/beneficios no son requisitos
            junk += [g for g in got if norm(n) == norm(g)]
        assert all(len(g.split()) <= 4 for g in got)
    mean = sum(rec_all) / len(rec_all)
    assert not junk, junk
    assert mean >= (0.85 if use_lexicon else 0.7), rec_all


def test_extractor_uses_profile_vocabulary_and_tfidf_corpus():
    job = Job(title="Analista", company="X", url="u", source="t", description="Trabajarás con Kaizen y reportes semanales en equipo. Requisitos: Kaizen, Excel.")
    assert "Kaizen" in extract_requirements_det(job, vocab=["Kaizen"])
    from app.ai.requirements import _Idf
    idf = _Idf(["alpha beta", "alpha gamma", "alpha delta", "beta"])
    assert idf.idf("alpha") < idf.idf("gamma")                                       # lo muy repetido entre ofertas pesa menos
    assert extract_requirements_det(job, corpus=["Excel contabilidad", "Excel ventas"])                  # el corpus no rompe nada


def _mini_profile():
    return Profile(full_name="Ana Pérez", headline="Analista de Seguridad", summary="Analista con foco en monitoreo. Disfruta aprender.",
                   skills=[Skill(name="Splunk"), Skill(name="SIEM"), Skill(name="Excel")],
                   experience=[Experience(company="Acme", title="Analista SOC", start="2019", end=None, bullets=[
                       "Monitoreé alertas en Splunk las 24 horas", "Respondí a incidentes de phishing y malware", "Elaboré reportes mensuales para la gerencia"]),
                               Experience(company="Beta", title="Soporte TI", start="2016", end="2018", bullets=["Atendí tickets de usuarios"])],
                   education=[Education(institution="UNAL", degree="Ingeniería", year="2016")], certifications=["CompTIA Security+"])


def test_det_classify_have_adjacent_missing_with_literal_evidence():
    p = _mini_profile()
    gaps = {g.keyword: g for g in classify(["SIEM", "respuesta a incidentes", "Kubernetes", "SAP CRM", "hemodiálisis", "Tableau"], p, None, set(), None)}
    assert gaps["SIEM"].status == GapStatus.HAVE
    adj = gaps["respuesta a incidentes"]
    assert adj.status == GapStatus.ADJACENT and adj.evidence == "Respondí a incidentes de phishing y malware"
    assert norm(adj.evidence) in norm("\n".join(p.experience[0].bullets))                        # evidencia LITERAL
    for k in ("Kubernetes", "SAP CRM", "hemodiálisis", "Tableau"):
        assert gaps[k].status == GapStatus.MISSING and not includable(gaps[k])
    # lo que el usuario confirma se vuelve incluible sin dejar de ser MISSING
    assert includable(classify(["Kubernetes"], p, None, {"Kubernetes"}, None)[0])


def test_det_classify_never_makes_named_tools_adjacent_nor_false_friends():
    p = _mini_profile()
    p.experience[0].bullets.append("Gestioné hemodinámica de pacientes críticos")                # falso amigo de 'hemodiálisis'
    g = {x.keyword: x for x in classify(["Darktrace", "hemodiálisis", "gestión de proyectos"], p, None, set(), None)}
    assert all(x.status == GapStatus.MISSING for x in g.values())


def test_det_classify_with_the_real_retriever_gives_same_conservative_result():
    p = _mini_profile()
    with Session(engine) as s:
        s.add(User(id=1, email="u@x.com", password_hash="x"))
        s.commit()
        r = retriever_for(s, 1, p)
        got = {x.keyword: x.status for x in classify(["respuesta a incidentes", "Kubernetes"], p, None, set(), None, retriever=r)}
    assert got == {"respuesta a incidentes": GapStatus.ADJACENT, "Kubernetes": GapStatus.MISSING}


def test_extractive_cv_selects_real_bullets_by_relevance_and_respects_the_golden_rule():
    p = _mini_profile()
    job = Job(title="Analista SOC", company="X", url="u", source="t", description="Requiere SIEM, Splunk y respuesta a incidentes. Deseable AWS.")
    gaps = [KeywordGap(keyword="SIEM", status=GapStatus.HAVE), KeywordGap(keyword="Splunk", status=GapStatus.HAVE),
            KeywordGap(keyword="respuesta a incidentes", status=GapStatus.ADJACENT, evidence="Respondí a incidentes de phishing y malware"),
            KeywordGap(keyword="AWS", status=GapStatus.MISSING)]
    cv = tailor_extractive(p, job, gaps)
    real = {b for e in p.experience for b in e.bullets}
    assert all(b in real for e in cv.experience for b in e["bullets"])                           # ninguna viñeta inventada ni reescrita
    assert cv.experience[0]["bullets"][-1] == "Elaboré reportes mensuales para la gerencia"       # la menos relevante, al final
    assert cv.experience[0]["bullets"][0] in ("Monitoreé alertas en Splunk las 24 horas", "Respondí a incidentes de phishing y malware")
    assert not contains(norm(cv.text), "AWS") and cv.skills[:2] == ["Splunk", "SIEM"]
    assert cv.trace[0].startswith("[sin-ia]") and "motivo=mode_none" in cv.trace[0]
    assert re.search(r"\d+ años de experiencia", cv.summary) and "Experiencia en SIEM y Splunk" in cv.summary and "Último puesto: Analista SOC en Acme" in cv.summary
    # confirmada por el usuario: ahora sí puede estar
    gaps[3].confirmed = True
    assert contains(norm(tailor_extractive(p, job, gaps).text), "AWS")


def test_extractive_summary_in_english_and_bullet_limits():
    p = _mini_profile()
    p.summary = ""
    p.experience[0].bullets = ["Monitored alerts in Splunk around the clock", "Handled phishing incidents"] * 5
    s = build_summary(p, Job(title="SOC Analyst", url="u", source="t", description="SIEM"), [KeywordGap(keyword="SIEM", status=GapStatus.HAVE)], "en")
    assert s.startswith("Analista de Seguridad with ") and "years of professional experience" in s and "Experience in SIEM." in s and "Most recent role" in s
    assert len(select_bullets(p.experience[0].bullets, [], set(), 6)) == 6 and len(select_bullets(["a"], [], set(), 6)) == 1


def test_extractive_cv_keeps_language_of_its_content_when_no_ai_can_translate():
    from app.ai.access import AiAccess
    from app.llm.guard import GuardedLLM
    p = _mini_profile()
    job = Job(title="SOC Analyst", company="X", url="u", source="t", description="We need SIEM and Splunk experience with incident response in a growing team.")
    gaps = [KeywordGap(keyword="SIEM", status=GapStatus.HAVE), KeywordGap(keyword="Splunk", status=GapStatus.HAVE)]
    off = GuardedLLM(None, AiAccess(False, "mode_none", "none"))
    cv = finalize_cv(tailor_cv(p, job, gaps, off), job, gaps, off, profile=p)
    assert cv.lang == "es" and "EXPERIENCIA LABORAL" in cv.text and any("sin IA no se traduce" in t for t in cv.trace)


# ---------------------------------------------------------------- calidad medida: none vs LLM simulado
def _compare(key):
    case, profile = CASES[key], _profile(key)
    job = case["job"]
    with Session(engine) as s:
        s.merge(User(id=1, email="q@x.com", password_hash="x"))
        s.commit()
        retr = retriever_for(s, 1, profile)
    ref = classify(case["keywords"], profile, ScriptedLLM([("adyacentes", {"adjacent": []})]), set())     # requisitos de referencia (los de un LLM)
    kws = extract_requirements_det(job, [x.name for x in profile.skills] + profile.certifications)
    g_none = classify(kws, profile, None, set(), job.title, retriever=retr)
    cv_none = finalize_cv(tailor_cv(profile, job, g_none, None), job, g_none, None, profile=profile)
    g_llm = classify(case["keywords"], profile, ScriptedLLM([("adyacentes", {"adjacent": []})]), set(), job.title, retriever=retr)
    w = CheatingWriter(profile)
    cv_llm = finalize_cv(tailor_cv(profile, job, g_llm, w), job, g_llm, w, profile=profile)
    return profile, ref, kws, cv_none, cv_llm, case


@pytest.mark.parametrize("key", list(CASES))
def test_none_mode_keeps_reasonable_ats_quality_per_profession(key):
    profile, ref, kws, cv_none, cv_llm, case = _compare(key)
    cov_none = check(cv_none.text, ref).keyword_coverage            # contra los MISMOS requisitos de referencia
    cov_llm = check(cv_llm.text, ref).keyword_coverage
    # el extractor recupera casi todo lo que pediría un LLM
    assert len(_hits(kws, case["keywords"])) / len(case["keywords"]) >= 0.8, kws
    assert cov_none >= 0.85 and cov_none >= cov_llm - 0.1
    assert cv_none.ats.score >= 85 and cv_none.ats.score >= cv_llm.ats.score - 10 and cv_none.ats.artifact_score >= 85
    # nada inventado: sin avisos de fidelidad, sin fugas de lo no confirmado, viñetas reales
    assert not cv_none.review_required and not any("no lo ha confirmado" in i for i in cv_none.ats.issues)
    for kw in case["missing"]:
        assert not contains(norm(cv_none.text), kw)
    real = {b for e in profile.experience for b in e.bullets}
    assert all(b in real for e in cv_none.experience for b in e["bullets"])
    assert cv_none.lang == ("en" if key.endswith("_en") else "es")
