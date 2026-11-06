"""Evals multi-profesión: perfil real (extraído de los CVs de fixtures_cv) + oferta -> brechas -> CV adaptado.
Comprueba (a) que nunca se cuelan palabras clave no confirmadas aunque el redactor intente colarlas y
(b) que el score ATS sube al incluir palabras clave respaldadas por el perfil."""
import re
from pathlib import Path

import pytest

from app.ats.checker import check
from app.ats.gaps import classify
from app.cv.finalize import finalize_cv
from app.cv.render import render_text
from app.cv.tailor import tailor_cv
from app.domain.job import Job
from app.domain.tailoring import GapStatus, TailoredCV, includable
from app.profile.builder import build_profile
from app.profile.extract import extract_document
from app.text import contains, norm
from tests.cv_fakes import DownLLM, ScriptedLLM
from tests.fixtures_cv.data import VARIANTS

FIX = Path(__file__).parent / "fixtures_cv"
FILES = {v[1]: f"{v[0]}.{v[2]}" for v in VARIANTS if v[3] != "scan"}      # un archivo por CV (último gana)

CASES = {
    "ventas_es": dict(
        job=Job(title="Ejecutivo Comercial B2B SaaS", company="Rappi", url="u", source="t", description=(
            "Buscamos un ejecutivo comercial con experiencia en venta consultiva B2B. Requisitos: manejo de Salesforce y HubSpot, "
            "cold calling, negociación, forecast de ventas y SAP CRM. Se valora Account Based Marketing y conocimientos de Tableau.")),
        keywords=["Salesforce", "HubSpot", "Cold calling", "Negociación", "Forecast de ventas", "SAP CRM", "Account Based Marketing"],
        missing={"SAP CRM", "Account Based Marketing"}, confirm="SAP CRM"),
    "contabilidad_es": dict(
        job=Job(title="Contador Senior", company="Bavaria", url="u", source="t", description=(
            "Contador con experiencia en NIIF, conciliaciones bancarias, declaración de renta e IVA. Manejo de SAP y Oracle NetSuite. "
            "Deseable Power BI y auditoría interna.")),
        keywords=["NIIF", "Conciliaciones bancarias", "Declaración de renta", "IVA", "SAP", "Oracle NetSuite", "Power BI", "Auditoría interna"],
        missing={"Oracle NetSuite", "Power BI"}, confirm="Power BI"),
    "enfermeria_es": dict(
        job=Job(title="Enfermero/a UCI", company="Hospital Clínic", url="u", source="t", description=(
            "Se necesita enfermera con experiencia en cuidados intensivos, ventilación mecánica, RCP avanzada, seguridad del paciente "
            "y historia clínica electrónica. Se valora hemodiálisis y triage.")),
        keywords=["Cuidados intensivos", "Ventilación mecánica", "RCP avanzada", "Seguridad del paciente", "Historia clínica electrónica", "Hemodiálisis", "Triage"],
        missing={"Hemodiálisis", "Triage"}, confirm="Triage"),
    "desarrollo_en": dict(
        job=Job(title="Senior Backend Engineer", company="Datadog", url="u", source="t", description=(
            "We are looking for a backend engineer with strong Python, FastAPI, PostgreSQL, Kubernetes and AWS experience. "
            "Terraform and CI/CD required. Nice to have: GraphQL and Rust.")),
        keywords=["Python", "FastAPI", "PostgreSQL", "Kubernetes", "AWS", "Terraform", "CI/CD", "GraphQL", "Rust"],
        missing={"GraphQL", "Rust"}, confirm="GraphQL"),
    "seguridad_en": dict(
        job=Job(title="SOC Analyst Tier 2", company="Mandiant", url="u", source="t", description=(
            "SOC analyst with hands-on SIEM, Splunk, MITRE ATT&CK and threat hunting experience. Knowledge of Microsoft Sentinel and "
            "Palo Alto Cortex XSOAR is a plus.")),
        keywords=["SIEM", "Splunk", "MITRE ATT&CK", "Threat Hunting", "Microsoft Sentinel", "Cortex XSOAR"],
        missing={"Microsoft Sentinel", "Cortex XSOAR"}, confirm="Microsoft Sentinel"),
}

class CheatingWriter:
    """Redactor simulado: usa las palabras clave permitidas, pero en el primer intento intenta colar las PROHIBIDAS
    (resumen y habilidades). Si el prompt trae correcciones, obedece."""

    def __init__(self, profile):
        self.profile = profile

    def complete_json(self, system, user):
        allowed = re.findall(r"^- (.+?) \((?:have|adjacent|missing)\)", user, re.M)
        banned = [b.strip() for b in user.split("PROHIBIDAS:")[1].split("\n")[0].split(",") if b.strip() and b.strip() != "ninguna"] if "PROHIBIDAS:" in user else []
        cheat = "CORRIGE ESTOS PROBLEMAS" not in user
        summary = f"{self.profile.summary} Experiencia en {', '.join(allowed[:6])}."
        if cheat and banned:
            summary += f" Además domino {banned[0]}."
        skills = [s.name for s in self.profile.skills] + allowed + (banned if cheat else [])
        return {"headline": self.profile.headline, "summary": summary, "skills": skills,
                "experience": [{"bullets": list(e.bullets)} for e in self.profile.experience]}

def _profile(key):
    ex = extract_document(FILES[key], (FIX / FILES[key]).read_bytes())
    return build_profile(ex.text, DownLLM())

def _gaps(case, profile, confirmed=()):
    llm = ScriptedLLM([("adyacentes", {"adjacent": []})])
    return classify(case["keywords"], profile, llm, set(confirmed))

def _tailor(case, profile, gaps):
    writer = CheatingWriter(profile)
    cv = tailor_cv(profile, case["job"], gaps, writer)
    return finalize_cv(cv, case["job"], gaps, writer)

@pytest.mark.parametrize("key", list(CASES))
def test_unconfirmed_keywords_never_leak_into_the_cv(key):
    case = CASES[key]
    profile = _profile(key)
    gaps = _gaps(case, profile)
    status = {g.keyword: g.status for g in gaps}
    assert {k for k, s in status.items() if s == GapStatus.MISSING} >= case["missing"]       # el perfil real no los tiene
    cv = _tailor(case, profile, gaps)
    text_n = norm(cv.text)
    for kw in case["missing"]:
        assert not contains(text_n, kw), f"{kw} se coló en el CV de {key}"
        assert not any(norm(s) == norm(kw) for s in cv.skills)
    assert not any("no lo ha confirmado" in i for i in cv.ats.issues)
    assert cv.lang == ("en" if key.endswith("_en") else "es")
    assert cv.ats.keyword_coverage >= 0.8

@pytest.mark.parametrize("key", list(CASES))
def test_ats_score_rises_with_backed_keywords_and_confirmation_unlocks_the_rest(key):
    case = CASES[key]
    profile = _profile(key)
    gaps = _gaps(case, profile)
    tailored = _tailor(case, profile, gaps)
    # CV sin adaptar: el del perfil tal cual, con apenas 3 habilidades y sin vocabulario de la oferta
    plain = TailoredCV(headline=profile.headline, summary="", skills=[s.name for s in profile.skills[:3]],
                       experience=[{"company": e.company, "title": e.title, "start": e.start, "end": e.end, "bullets": e.bullets[:1]} for e in profile.experience],
                       education=[e.model_dump() for e in profile.education], languages=profile.languages,
                       contact={"full_name": profile.full_name, "email": profile.email, "phone": profile.phone, "location": profile.location})
    base = check(render_text(plain, tailored.lang), gaps)
    assert tailored.ats.score > base.score and tailored.ats.keyword_coverage > base.keyword_coverage
    # el usuario confirma una habilidad que le falta: ahora sí puede entrar y la cobertura sube
    gaps2 = _gaps(case, profile, {case["confirm"]})
    assert any(g.keyword == case["confirm"] and includable(g) and g.status == GapStatus.MISSING for g in gaps2)
    cv2 = _tailor(case, profile, gaps2)
    assert contains(norm(cv2.text), case["confirm"])
    others = case["missing"] - {case["confirm"]}
    assert not any(contains(norm(cv2.text), k) for k in others)
    assert cv2.ats.keyword_coverage >= tailored.ats.keyword_coverage

def test_adjacent_evidence_must_come_from_the_real_profile():
    case = CASES["ventas_es"]
    profile = _profile("ventas_es")
    real = profile.experience[0].bullets[0]
    llm = ScriptedLLM([("adyacentes", {"adjacent": [{"keyword": "SAP CRM", "evidence": "Implementé SAP CRM en 30 países"},
                                                     {"keyword": "Account Based Marketing", "evidence": real}]})])
    st = {g.keyword: g.status for g in classify(case["keywords"], profile, llm)}
    assert st["SAP CRM"] == GapStatus.MISSING                           # evidencia inventada: rechazada
    assert st["Account Based Marketing"] == GapStatus.ADJACENT          # evidencia real: aceptada
