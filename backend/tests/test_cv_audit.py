"""Auditoría de fidelidad: un redactor simulado que INVENTA cifras, empresas, cargos, herramientas, certificaciones o idiomas debe ser atrapado,
y uno fiel (literal o con paráfrasis) no debe dar falsos positivos."""
import copy

import pytest

from app.cv.audit import audit_and_repair, audit_cv, audit_texts, repair_cv, review_with_llm
from app.domain.job import Job
from app.domain.profile import Education, Experience, Profile, Skill
from app.domain.tailoring import GapStatus, KeywordGap, TailoredCV
from app.rag.embeddings import HashEmbedder
from tests.cv_fakes import ScriptedLLM

EMB = HashEmbedder()

PROFILE = Profile(
    full_name="Luis Gómez", email="luis@x.com", phone="+57 300 111 2233", location="Bogotá", headline="Analista de ciberseguridad",
    summary="Analista con 6 años en monitoreo de seguridad y soporte de TI.",
    skills=[Skill(name="SIEM"), Skill(name="Splunk"), Skill(name="Python"), Skill(name="Active Directory")],
    experience=[
        Experience(company="Acme", title="Analista de Seguridad", start="2021", end=None,
                   bullets=["Monitoreé alertas en Splunk las 24 horas", "Respondí a incidentes de phishing y malware reduciendo el tiempo de contención en 30%",
                            "Documenté procedimientos de respuesta para el equipo de 5 analistas"]),
        Experience(company="Banco Andino", title="Soporte TI", start="2018", end="2021",
                   bullets=["Atendí 120 tickets mensuales de usuarios internos", "Administré cuentas en Active Directory y Office 365"])],
    education=[Education(institution="UNAL", degree="Ingeniería de Sistemas", year="2018")],
    languages=["Español", "Inglés"], certifications=["CompTIA Security+"])

JOB = Job(title="Analista SOC", company="Bancolombia", location="Bogotá", url="https://b/1", source="t", description="SIEM, Splunk y respuesta a incidentes")
GAPS = [KeywordGap(keyword="SIEM", status=GapStatus.HAVE), KeywordGap(keyword="Splunk", status=GapStatus.HAVE),
        KeywordGap(keyword="respuesta a incidentes", status=GapStatus.ADJACENT, evidence="Respondí a incidentes de phishing y malware"),
        KeywordGap(keyword="Kubernetes", status=GapStatus.MISSING), KeywordGap(keyword="terraform", status=GapStatus.MISSING)]


def faithful() -> TailoredCV:
    """Lo que haría un redactor honesto: reordena, parafrasea con el vocabulario de la oferta y no añade nada."""
    return TailoredCV(
        headline="Analista de ciberseguridad | Analista SOC",
        summary="Analista SOC con 6 años en monitoreo de seguridad y soporte de TI. Experiencia en SIEM y respuesta a incidentes.",
        skills=["SIEM", "Splunk", "Python", "Active Directory"],
        experience=[
            {"company": "Acme", "title": "Analista de Seguridad", "start": "2021", "end": None,
             "bullets": ["Monitoreé alertas SIEM en Splunk durante las 24 horas", "Atendí la respuesta a incidentes de phishing y malware, reduciendo 30% el tiempo de contención",
                         "Documenté procedimientos de respuesta para el equipo de 5 analistas"]},
            {"company": "Banco Andino", "title": "Soporte TI", "start": "2018", "end": "2021",
             "bullets": ["Atendí 120 tickets mensuales de usuarios internos", "Administré cuentas en Active Directory y Office 365"]}],
        education=[{"institution": "UNAL", "degree": "Ingeniería de Sistemas", "year": "2018"}], certifications=["CompTIA Security+"], languages=["Español", "Inglés"],
        contact={"full_name": "Luis Gómez", "email": "luis@x.com", "phone": "+57 300 111 2233", "location": "Bogotá"}, lang="es")


def audit(cv):
    return audit_cv(cv, PROFILE, GAPS, JOB, embedder=EMB)


def flagged(report, section=None):
    return [ln for ln in report.lines if ln.status == "unsupported" and ln.severity == "high" and (section is None or ln.section == section)]


def test_faithful_draft_has_no_false_positives():
    r = audit(faithful())
    assert r.verdict == "ok", [(ln.section, ln.text, [c.note for c in ln.claims if not c.supported]) for ln in r.lines if ln.status == "unsupported"]
    assert r.counts["unsupported"] == 0 and r.counts["reworded"] >= 2 and r.counts["supported"] >= 8
    b = next(ln for ln in r.lines if ln.text.startswith("Monitoreé alertas SIEM"))
    assert b.status == "reworded" and b.source_ref == "experience:0:bullet:0" and "Splunk" in b.source_text


def test_literal_copy_is_supported_with_origin():
    cv = faithful()
    cv.experience[0]["bullets"][0] = "Monitoreé alertas en Splunk las 24 horas"
    ln = next(ln for ln in audit(cv).lines if ln.section == "bullet" and ln.exp_index == 0 and ln.index == 0)
    assert ln.status == "supported" and ln.source_ref == "experience:0:bullet:0" and ln.score == 1.0


# ---- el redactor tramposo: cada invención por separado
def _mutate(fn):
    cv = copy.deepcopy(faithful())
    fn(cv)
    return cv


INVENTIONS = {
    "cifra inventada": (lambda cv: cv.experience[0]["bullets"].__setitem__(0, "Monitoreé alertas en Splunk las 24 horas y reduje 45% los falsos positivos"), "bullet", "45"),
    "cifra de equipo inventada": (lambda cv: cv.experience[0]["bullets"].__setitem__(2, "Documenté procedimientos de respuesta para el equipo de 15 analistas"), "bullet", "15"),
    "empresa inventada en una viñeta": (lambda cv: cv.experience[0]["bullets"].__setitem__(0, "Monitoreé alertas en Splunk las 24 horas para clientes como Google"), "bullet", "Google"),
    "herramienta inventada (siglas)": (lambda cv: cv.experience[0]["bullets"].__setitem__(0, "Monitoreé alertas en Splunk y QRadar las 24 horas"), "bullet", "QRadar"),
    "herramienta inventada (CamelCase)": (lambda cv: cv.experience[1]["bullets"].__setitem__(1, "Administré cuentas en Active Directory y ServiceNow"), "bullet", "ServiceNow"),
    "habilidad inventada": (lambda cv: cv.skills.append("Docker"), "skill", "Docker"),
    "empresa inventada (experiencia)": (lambda cv: cv.experience.append({"company": "Globant", "title": "Analista de Seguridad", "start": "2015", "end": "2018", "bullets": ["Monitoreé alertas en Splunk las 24 horas"]}), "experience", "Globant"),
    "cargo cambiado": (lambda cv: cv.experience[0].update(title="Director de Seguridad"), "experience", "Director"),
    "fecha cambiada": (lambda cv: cv.experience[1].update(start="2012"), "experience", "2012"),
    "fin de empleo borrado (sigue vigente)": (lambda cv: cv.experience[1].update(end=None), "experience", "actualidad"),
    "certificación inventada": (lambda cv: cv.certifications.append("CISSP"), "certification", "CISSP"),
    "idioma inventado": (lambda cv: cv.languages.append("Alemán"), "language", "Alemán"),
    "estudio inventado": (lambda cv: cv.education.append({"institution": "MIT", "degree": "Máster en Ciberseguridad", "year": "2020"}), "education", "MIT"),
    "años inflados en el resumen": (lambda cv: setattr(cv, "summary", "Analista SOC con 12 años en monitoreo de seguridad y soporte de TI."), "summary", "12"),
    "logro completamente inventado": (lambda cv: cv.experience[0]["bullets"].append("Lideré la migración de la plataforma de seguridad a la nube junto a la gerencia"), "bullet", None),
    "viñeta de otra experiencia": (lambda cv: cv.experience[0]["bullets"].__setitem__(0, "Atendí 120 tickets mensuales de usuarios internos"), "bullet", None),
    "palabra clave no confirmada en minúsculas": (lambda cv: cv.experience[0]["bullets"].__setitem__(0, "Monitoreé alertas en Splunk y desplegué en kubernetes las 24 horas"), "bullet", "Kubernetes"),
    "contacto alterado": (lambda cv: cv.contact.update(email="otro@gmail.com"), "contact", "otro@gmail.com"),
    "titular con otro rol": (lambda cv: setattr(cv, "headline", "CISO en Telefónica"), "headline", None),
}


@pytest.mark.parametrize("name", list(INVENTIONS))
def test_cheating_writer_is_caught(name):
    fn, section, needle = INVENTIONS[name]
    r = audit(_mutate(fn))
    hits = flagged(r, section)
    assert hits, f"no se detectó: {name}"
    assert r.verdict == "fail" and r.requires_review
    if needle:
        assert any(needle.lower() in (ln.text + " " + " ".join(c.text + " " + c.note for c in ln.claims)).lower() for ln in hits)


def test_scoreboard_all_inventions_at_once():
    """Un redactor que mete todo a la vez: se marca cada línea falsa y NINGUNA de las fieles."""
    cv = faithful()
    for fn, _, _ in INVENTIONS.values():
        fn(cv)
    r = audit(cv)
    faithful_lines = [ln for ln in r.lines if ln.text in ("SIEM", "Splunk", "Python", "Español", "Inglés", "CompTIA Security+")]
    assert len(faithful_lines) == 6 and all(ln.status == "supported" for ln in faithful_lines)
    assert len(flagged(r)) >= 12


def test_repair_removes_everything_invented_and_keeps_the_rest():
    cv = faithful()
    for k in ("cifra inventada", "empresa inventada (experiencia)", "habilidad inventada", "certificación inventada", "idioma inventado", "logro completamente inventado"):
        INVENTIONS[k][0](cv)
    fixed, rep = audit_and_repair(cv, PROFILE, GAPS, JOB, embedder=EMB)
    assert rep.verdict == "ok" and rep.repaired
    assert [e["company"] for e in fixed.experience] == ["Acme", "Banco Andino"]
    assert "Docker" not in fixed.skills and "CISSP" not in fixed.certifications and "Alemán" not in fixed.languages
    assert all("45" not in b for e in fixed.experience for b in e["bullets"])
    assert fixed.experience[0]["bullets"][0] == "Monitoreé alertas en Splunk las 24 horas"     # la inventada se sustituye por la original del perfil
    assert len(fixed.experience[0]["bullets"]) == 3 and "Lideré la migración" not in " ".join(fixed.experience[0]["bullets"])


def test_repair_leaves_review_flag_if_it_cannot_fix():
    r = audit(_mutate(INVENTIONS["titular con otro rol"][0]))
    cv, acts = repair_cv(_mutate(INVENTIONS["titular con otro rol"][0]), PROFILE, r)
    assert cv.headline == PROFILE.headline and acts


def test_confirmed_keywords_and_adjacent_vocabulary_are_allowed():
    gaps = [*GAPS[:3], KeywordGap(keyword="Kubernetes", status=GapStatus.MISSING, confirmed=True)]
    cv = _mutate(lambda c: c.skills.append("Kubernetes"))
    assert audit_cv(cv, PROFILE, gaps, JOB, embedder=EMB).verdict == "ok"
    assert audit_cv(cv, PROFILE, GAPS, JOB, embedder=EMB).verdict == "fail"          # sin confirmar sí es invención


def test_experience_years_derived_from_dates_are_supported():
    cv = _mutate(lambda c: setattr(c, "summary", "Analista SOC con más de 5 años de experiencia en seguridad."))   # 2018 -> hoy
    ln = next(ln for ln in audit(cv).lines if ln.section == "summary")
    assert not any(not c.supported and c.severity == "high" for c in ln.claims)


def test_24_7_equals_24_horas_but_other_numbers_do_not():
    assert audit(faithful()).verdict == "ok"           # '24/7' / '24 horas' en las viñetas del redactor fiel
    cv = _mutate(lambda c: c.experience[0]["bullets"].__setitem__(0, "Monitoreé alertas en Splunk en turnos 24/7"))
    assert audit(cv).verdict == "ok"


def test_report_is_per_line_for_highlighting():
    r = audit(_mutate(INVENTIONS["cifra inventada"][0]))
    bad = flagged(r, "bullet")[0]
    assert bad.exp_index == 0 and bad.index == 0 and bad.source_ref == "experience:0:bullet:0"
    comp = r.compact()
    assert comp["verdict"] == "fail" and any(f["text"].endswith("falsos positivos") for f in comp["flagged"])


def test_llm_reviewer_only_adds_suspicion_never_clears():
    cv = faithful()
    base = audit(cv)
    llm = ScriptedLLM([("REVISOR independiente", {"unsupported": [{"line": 3, "reason": "alcance inflado"}, {"line": 99, "reason": "x"}]})])
    out = review_with_llm(cv, PROFILE, base, llm)
    assert out.llm_review and out.verdict == "warn" and any(c.kind == "llm" for ln in out.lines for c in ln.claims)
    # el LLM nunca absuelve lo que la regla marcó
    bad_cv = _mutate(INVENTIONS["cifra inventada"][0])
    llm2 = ScriptedLLM([("REVISOR independiente", {"unsupported": []})])
    assert review_with_llm(bad_cv, PROFILE, audit(bad_cv), llm2).verdict == "fail"
    # y si el LLM falla no rompe nada
    from tests.cv_fakes import DownLLM
    assert review_with_llm(cv, PROFILE, base, DownLLM()).verdict == "ok"


def test_llm_reviewer_is_off_by_default(monkeypatch):
    llm = ScriptedLLM([("REVISOR independiente", {"unsupported": [{"line": 0, "reason": "x"}]})])
    audit_cv(faithful(), PROFILE, GAPS, JOB, embedder=EMB, llm=llm)
    assert llm.calls == []
    monkeypatch.setenv("JH_AUDIT_LLM_REVIEW", "1")
    audit_cv(faithful(), PROFILE, GAPS, JOB, embedder=EMB, llm=llm)
    assert len(llm.calls) == 1


def test_audit_texts_same_auditor_for_other_outputs():
    lines = audit_texts([("bullet", "Monitoreé alertas en Splunk las 24 horas"), ("bullet", "Reduje costos 40% con Terraform en Google")], PROFILE, GAPS, JOB, embedder=EMB)
    assert lines[0].status == "supported" and lines[1].status == "unsupported" and lines[1].severity == "high"


def test_embeddings_failure_degrades_to_lexical():
    class Broken:
        name, sim_lo, sim_hi = "x", 0.0, 1.0

        def embed(self, texts, kind="doc"):
            raise RuntimeError("caído")
    assert audit_cv(faithful(), PROFILE, GAPS, JOB, embedder=Broken()).verdict == "ok"
