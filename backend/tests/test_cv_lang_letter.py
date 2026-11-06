import pytest

from app.cv.cover_letter import generate_cover_letter, template_letter, validate_letter
from app.cv.finalize import finalize_cv, translate_cv, validate_translation
from app.cv.lang import detect_lang, detect_lang_confidence, headings
from app.domain.job import Job
from app.domain.profile import Education, Experience, Profile, Skill
from app.domain.tailoring import GapStatus, KeywordGap
from app.text import contains, norm
from tests.cv_fakes import DownLLM, ScriptedLLM
from tests.test_cv_render import make_cv

JOB_ES = Job(title="Analista SOC", company="Bancolombia", url="u", source="t",
             description="Buscamos un analista con experiencia en SIEM, respuesta a incidentes y conocimientos de AWS. Ofrecemos contrato indefinido y un equipo con muchas ganas.")
JOB_EN = Job(title="SOC Analyst", company="Barclays", url="u", source="t",
             description="We are looking for a SOC analyst with strong experience in SIEM and incident response. You will join our team and work across the company.")
PROFILE = Profile(full_name="Luis Gómez", email="luis@x.com", headline="Analista de ciberseguridad", summary="Analista con 4 años en monitoreo de seguridad.",
                  skills=[Skill(name="SIEM"), Skill(name="Splunk")], languages=["Español", "Inglés"],
                  experience=[Experience(company="Acme", title="Analista de Seguridad", start="2021-03", bullets=["Monitoreé alertas en Splunk las 24 horas", "Respondí a incidentes de phishing y malware"])],
                  education=[Education(institution="UNAL", degree="Ingeniería de Sistemas", year="2020")])
GAPS = [KeywordGap(keyword="SIEM", status=GapStatus.HAVE),
        KeywordGap(keyword="respuesta a incidentes", status=GapStatus.ADJACENT, evidence="Respondí a incidentes de phishing y malware"),
        KeywordGap(keyword="AWS", status=GapStatus.MISSING)]

def test_language_detection_on_job_ads_and_short_texts():
    assert detect_lang(JOB_ES.description) == "es" and detect_lang(JOB_EN.description) == "en"
    assert detect_lang("Requisitos: SIEM, Splunk y experiencia en respuesta a incidentes") == "es"
    assert detect_lang("Requirements: strong SIEM and Splunk experience, and you will join the team") == "en"
    assert detect_lang("Python Django AWS") == "es"                   # sin señal: por defecto
    assert detect_lang("Python Django AWS", default="en") == "en"
    assert detect_lang_confidence("") == ("es", 0.0)
    assert headings("en")["experience"] == "WORK EXPERIENCE" and headings("xx")["experience"] == "EXPERIENCIA LABORAL"

def test_finalize_sets_job_language_headings_and_rechecks_ats():
    cv = finalize_cv(make_cv(), JOB_ES, GAPS)
    assert cv.lang == "es" and "EXPERIENCIA LABORAL" in cv.text and cv.ats and cv.ats.lang == "es"
    en = finalize_cv(make_cv(), JOB_EN, GAPS)                       # sin writer: solo encabezados/fechas en inglés
    assert en.lang == "en" and "WORK EXPERIENCE" in en.text and "Mar 2021 - Present" in en.text
    assert not any("sección" in i for i in en.ats.issues)           # el verificador acepta ambos idiomas

SEGS_ES = ["Analista SOC", "Analista con 4 años en SIEM.", "Monitoreé alertas en Splunk las 24 horas", "Respondí a incidentes de phishing y malware", "Atendí 40 tickets semanales"]
GOOD_EN = ["SOC Analyst", "Analyst with 4 years in SIEM.", "Monitored alerts in Splunk 24 hours a day", "Responded to phishing and malware incidents", "Handled 40 weekly tickets"]

def test_translation_is_applied_only_when_guards_pass():
    cv = make_cv(headline="Analista SOC", summary="Analista con 4 años en SIEM.")
    out = finalize_cv(cv, JOB_EN, GAPS, ScriptedLLM([("traductor", {"textos": GOOD_EN})]))
    assert out.summary == "Analyst with 4 years in SIEM." and out.experience[0]["bullets"][1] == "Responded to phishing and malware incidents"
    assert out.experience[1]["bullets"] == ["Handled 40 weekly tickets"]
    assert any("traducido a en" in t for t in out.trace) and "PROFESSIONAL SUMMARY" in out.text
    assert out.experience[0]["company"] == "Acme" and out.experience[0]["start"] == "2021-03"      # empresas, cargos y fechas no se tocan
    assert cv.summary == "Analista con 4 años en SIEM."                                           # el original no se muta

@pytest.mark.parametrize("mutate,expect", [
    (lambda t: t.append("Extra"), "textos"),
    (lambda t: t.__setitem__(3, "Responded to 99 incidents"), "cifras"),
    (lambda t: t.__setitem__(2, "Monitored alerts in Splunk 24 hours a day using AWS"), "AWS"),
    (lambda t: t.__setitem__(1, "Analyst with 4 years."), "SIEM"),
    (lambda t: t.__setitem__(4, ""), "vacío"),
])
def test_translation_guards_reject_inventions_and_losses(mutate, expect):
    new = list(GOOD_EN)
    mutate(new)
    problems = validate_translation(SEGS_ES, {"textos": new}, GAPS)
    assert any(expect in p for p in problems), problems
    assert validate_translation(SEGS_ES, "no json", GAPS) and validate_translation(SEGS_ES, {"textos": GOOD_EN}, GAPS) == []

def test_failed_translation_keeps_original_content():
    cv = make_cv()
    out, note = translate_cv(cv, "en", ScriptedLLM([("traductor", {"textos": ["x"]})]), GAPS)
    assert out is cv and "descartada" in note
    out2, note2 = translate_cv(cv, "en", DownLLM(), GAPS)
    assert out2 is cv and "omitida" in note2

LETTER_OK = ("Estimado equipo de Bancolombia:\n\nMe dirijo a ustedes para postularme al puesto de Analista SOC. Soy analista de ciberseguridad y en Acme he trabajado como Analista de "
             "Seguridad, donde monitoreé alertas en Splunk las 24 horas y respondí a incidentes de phishing y malware.\n\nMi experiencia con SIEM y respuesta a incidentes se alinea con lo "
             "que buscan. Cuento con 4 años en monitoreo de seguridad y me gusta trabajar en equipo para contener amenazas y mejorar los procesos de detección y respuesta de la organización "
             "con rigor y disciplina.\n\nAgradezco su tiempo y quedo atento a la posibilidad de conversar en una entrevista sobre cómo puedo aportar al equipo desde el primer día.\n\nAtentamente,\nLuis Gómez")

def test_cover_letter_accepts_clean_llm_letter():
    cl = generate_cover_letter(PROFILE, JOB_ES, GAPS, ScriptedLLM([("redactor de cartas", {"letter": LETTER_OK})]))
    assert cl.source == "llm" and cl.lang == "es" and cl.issues == [] and cl.text.rstrip().endswith("Luis Gómez")

@pytest.mark.parametrize("bad,why", [
    ("Además tengo experiencia en AWS y Kubernetes.", "AWS"),
    ("He liderado equipos de 45 personas en Google.", "Google"),
    ("Tengo 12 años de experiencia en SOC.", "12"),
    ("Soy [Nombre] y trabajé en {{empresa}}.", "marcadores"),
])
def test_cover_letter_guards_detect_invention(bad, why):
    issues = validate_letter(LETTER_OK.replace("Atentamente,", bad + "\n\nAtentamente,"), PROFILE, JOB_ES, GAPS, "es")
    assert any(why in i for i in issues), issues

def test_cover_letter_retries_with_feedback_then_strips_bad_sentences():
    cheat = LETTER_OK.replace("Cuento con 4 años", "Domino AWS y Kubernetes. Cuento con 4 años")
    llm = ScriptedLLM([("redactor de cartas", [{"letter": cheat}, {"letter": cheat}])])
    cl = generate_cover_letter(PROFILE, JOB_ES, GAPS, llm)
    assert len(llm.calls) == 2 and "CORRIGE" in llm.calls[1][1]
    assert not contains(norm(cl.text), "AWS") and not contains(norm(cl.text), "Kubernetes")
    assert cl.source == "llm" and any("eliminaron" in t for t in cl.trace)

def test_cover_letter_confirmed_keyword_is_allowed():
    gaps = [g.model_copy() for g in GAPS]
    gaps[2].confirmed = True
    text = LETTER_OK.replace("Mi experiencia con SIEM", "Mi experiencia con SIEM y AWS")
    assert not [i for i in validate_letter(text, PROFILE, JOB_ES, gaps, "es") if "AWS" in i]

def test_cover_letter_falls_back_to_template_in_both_languages():
    es = generate_cover_letter(PROFILE, JOB_ES, GAPS, DownLLM())
    assert es.source == "template" and es.lang == "es" and es.text.startswith("Estimado equipo de Bancolombia") and es.text.rstrip().endswith("Luis Gómez")
    assert "AWS" not in es.text and "SIEM" in es.text and "Analista de Seguridad en Acme" in es.text
    en = generate_cover_letter(PROFILE, JOB_EN, [KeywordGap(keyword="SIEM", status=GapStatus.HAVE)], ScriptedLLM([("redactor", {"letter": ""})]))
    assert en.lang == "en" and en.source == "template" and en.text.startswith("Dear Barclays hiring team") and "Sincerely" in en.text
    assert validate_letter(template_letter(PROFILE, JOB_ES, GAPS, "es"), PROFILE, JOB_ES, GAPS, "es") == [i for i in validate_letter(es.text, PROFILE, JOB_ES, GAPS, "es")]

def test_cover_letter_wrong_language_is_flagged():
    assert any("idioma" in i for i in validate_letter(LETTER_OK, PROFILE, JOB_EN, GAPS, "en"))

def test_empty_greeting_is_replaced():
    from app.cv.cover_letter import fix_greeting
    assert fix_greeting("Estimado/a ,\n\nHola", JOB_ES, "es").startswith("Estimado equipo de Bancolombia:")
    assert fix_greeting("Dear,\n\nHi", JOB_EN, "en").startswith("Dear Barclays hiring team,")
    assert fix_greeting("Estimado Juan Pérez:\n\nHola", JOB_ES, "es").startswith("Estimado Juan")

def test_translation_tolerates_legit_changes_in_numbers_format_and_acronyms():
    orig = ["Gestioné ventas de COP 2.800 millones con el área de TI", "Aumenté la tasa de cierre del 18% al 27% usando Negociación y Salesforce"]
    ok = {"textos": ["Managed sales of COP 2,800 million with the IT department", "Raised the close rate from 18% to 27% using Negotiation and Salesforce"]}
    assert validate_translation(orig, ok, []) == []
    assert validate_translation(orig, {"textos": [ok["textos"][0], "Raised the close rate from 18% to 72% using Negotiation and Salesforce"]}, [])
