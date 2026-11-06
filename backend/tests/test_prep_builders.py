"""Preparación de entrevista: todo lo que se afirma del candidato sale literal de su perfil (con cita), las brechas se presentan como
'cómo responder con honestidad', y nada de cifras de mercado inventadas."""
import json
import re

from app.domain.job import Job
from app.domain.tailoring import GapStatus, KeywordGap
from app.prep import builders as b
from app.prep.competencies import hits, job_competencies
from tests.cv_fakes import ScriptedLLM
from tests.test_cv_audit import GAPS, PROFILE

DESC = ("Buscamos analista SOC con trabajo en equipo, comunicación con stakeholders, resolución de incidentes y liderazgo. "
        "Manejo de SIEM, Splunk y Kubernetes. Modalidad remoto.")
JOB_ES = Job(title="Analista SOC", company="Bancolombia", location="Bogotá", url="u", source="s", language="es", description=DESC, work_mode="remote",
             salary="COP 6.000.000 - 8.000.000", salary_min=6e6, salary_max=8e6, salary_currency="COP", salary_period="month")
BULLETS = {x for e in PROFILE.experience for x in e.bullets}


def _strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)


def test_competencies_detected_in_offer_and_in_bullets():
    comps = dict(job_competencies(DESC))
    assert {"teamwork", "communication", "problem_solving", "leadership"} <= set(comps)
    assert hits("Colaboré con el equipo de TI", "teamwork") >= 2 and hits("Lideré a 3 personas", "leadership") == 1
    assert [k for k, _ in job_competencies("Texto sin nada")][:3] == ["teamwork", "problem_solving", "results"]    # relleno marcado con 0 menciones


def test_stories_come_only_from_real_bullets_with_origin_and_audit():
    st = b.build_stories(PROFILE, JOB_ES, GAPS, "es")
    assert st["stories"]
    for s in st["stories"]:
        assert s["star"]["action"]["text"] in BULLETS and s["audit"]["status"] == "supported"
        assert s["source"]["ref"].startswith("experience:") and s["source"]["text"] == s["star"]["action"]["text"]
        assert s["source"]["company"] in ("Acme", "Banco Andino")
        # lo que el perfil no tiene NO se rellena: queda como pregunta para el usuario
        for part in ("task", "result"):
            p = s["star"][part]
            assert (p["text"] is None and p["origin"] == "user" and p["prompt"]) or (p["origin"] == "profile" and p["text"] in BULLETS)
    assert any(u["competency"] == "leadership" and "no inventes" in u["message"] for u in st["uncovered"])     # sin viñeta de liderazgo: se dice, no se inventa


def test_result_is_only_filled_when_the_bullet_has_a_real_figure():
    st = b.build_stories(PROFILE, JOB_ES, GAPS, "es")["stories"]
    for s in st:
        has_num = bool(re.search(r"\d", s["star"]["action"]["text"]))
        assert (s["star"]["result"]["text"] is not None) == has_num


def test_questions_gaps_are_honest_answers_not_claims():
    st = b.build_stories(PROFILE, JOB_ES, GAPS, "es")
    q = b.build_questions(PROFILE, JOB_ES, GAPS, st, "es")
    tech = {x["keyword"] for x in q["questions"] if x["type"] == "technical"}
    assert {"SIEM", "Splunk"} <= tech and "Kubernetes" not in tech and "terraform" not in tech        # solo lo respaldado como pregunta de experiencia
    splunk = next(x for x in q["questions"] if x.get("keyword") == "Splunk")
    assert any("Splunk" in s["text"] for s in splunk["sources"])
    gaps = {x["keyword"]: x for x in q["honest_gaps"]}
    assert set(gaps) == {"Kubernetes", "terraform"}
    steps = gaps["Kubernetes"]["how_to_answer"]
    assert "No lo he usado" in steps[0] and "no fuerces" in steps[1] and "no lo menciones como experiencia" in steps[-1]
    beh = [x for x in q["questions"] if x["type"] == "behavioral"]
    assert beh and all(x["sources"] == [] or x["sources"][0]["text"] in BULLETS for x in beh)


def test_adjacent_gap_cites_real_evidence():
    gaps = [KeywordGap(keyword="Active Directory Federation", status=GapStatus.MISSING)]
    q = b.build_questions(PROFILE, JOB_ES, gaps, {"stories": [], "uncovered": []}, "es")
    ev = q["honest_gaps"][0]["adjacent_evidence"]
    assert ev and "Active Directory" in ev[0]["text"] and ev[0]["ref"].startswith(("skill:", "experience:"))


def test_questions_to_ask_are_grounded_in_the_offer():
    ask = b.build_ask(PROFILE, JOB_ES, GAPS, "es")["ask"]
    text = " ".join(a["question"] for a in ask)
    assert "SIEM" in text and "Kubernetes" in text and "remoto" in text and "rango salarial" not in text       # la oferta SÍ publica salario
    no_sal = JOB_ES.model_copy(update={"salary": None, "salary_min": None, "salary_max": None})
    assert any("rango salarial" in a["question"] for a in b.build_ask(PROFILE, no_sal, GAPS, "es")["ask"])
    assert all(a["why"] for a in ask)


def test_negotiation_uses_only_real_data_and_declares_what_is_unknown():
    n = b.build_negotiation(PROFILE, JOB_ES, GAPS, "es", 7e6, 9e6, "COP")
    assert n["offer"]["published"] and n["comparison"]["relation"] == "overlap" and n["user_range"]["min"] == 7e6
    assert any("COP 6.000.000" in k for k in n["known"]) and any("7.000.000" in k for k in n["known"])
    assert any("no tenemos datos fiables" in u for u in n["unknown"]) and "No inventamos cifras" in n["disclaimer"]
    assert n["leverage"]["requirements_you_meet"] and all(r["sources"] for r in n["leverage"]["requirements_you_meet"])
    assert b.build_negotiation(PROFILE, JOB_ES, GAPS, "es", 9e6, 12e6, "COP")["comparison"]["relation"] == "above"
    assert b.build_negotiation(PROFILE, JOB_ES, GAPS, "es", 3e6, 5e6, "COP")["comparison"]["relation"] == "below"
    diff = b.build_negotiation(PROFILE, JOB_ES, GAPS, "es", 1500, 2500, "USD")
    assert diff["comparison"] is None and any("monedas distintas" in u for u in diff["unknown"])


def test_negotiation_without_offer_salary_or_user_range():
    job = JOB_ES.model_copy(update={"salary": None, "salary_min": None, "salary_max": None})
    n = b.build_negotiation(PROFILE, job, GAPS, "es")
    assert not n["offer"]["published"] and n["user_range"] is None and n["comparison"] is None and n["known"] == []
    assert any("no publica salario" in u for u in n["unknown"]) and any("No has indicado tu rango" in u for u in n["unknown"])


def test_no_invented_market_figures_anywhere():
    """Ninguna cifra monetaria/porcentual en ninguna salida salvo las que da la oferta, el perfil o el usuario."""
    st = b.build_stories(PROFILE, JOB_ES, GAPS, "es")
    out = {"stories": st, "questions": b.build_questions(PROFILE, JOB_ES, GAPS, st, "es"), "ask": b.build_ask(PROFILE, JOB_ES, GAPS, "es"),
           "neg": b.build_negotiation(PROFILE, JOB_ES, GAPS, "es", 7e6, 9e6, "COP")}
    allowed = " ".join([JOB_ES.model_dump_json(), PROFILE.model_dump_json(), "7.000.000 9.000.000 6.000.000 8.000.000"])
    for sring in _strings(out):
        for m in re.findall(r"\d{1,3}(?:\.\d{3})+|\d+\s?%|\$\s?\d+", sring):
            assert m in allowed or m.replace(".", "") in allowed.replace(".", ""), (m, sring)


def test_english_job_gets_english_output():
    en = Job(title="SOC Analyst", company="Acme", url="u", source="s", description="We are looking for a SOC analyst with teamwork, communication and incident response. You will work with SIEM.")
    assert b.lang_for(en) == "en" and b.lang_for(JOB_ES) == "es" and b.lang_for(JOB_ES, "en") == "en"
    st = b.build_stories(PROFILE, en, GAPS, "en")
    q = b.build_questions(PROFILE, en, GAPS, st, "en")
    assert any("How have you used" in x["question"] for x in q["questions"]) and st["stories"][0]["star"]["task"]["prompt"].startswith("What was")
    assert any("success look like" in a["question"] for a in b.build_ask(PROFILE, en, GAPS, "en")["ask"])
    assert "Consejos" not in json.dumps(b.build_negotiation(PROFILE, en, GAPS, "en"))


def test_upskill_priority_follows_frequency_across_saved_jobs():
    def gap(kw, st="missing", **k):
        return {"keyword": kw, "status": st, "confirmed": False, **k}
    jobs = [
        (1, {"title": "A", "company": "X"}, "applied", 80, [gap("Kubernetes"), gap("Terraform"), gap("SIEM", "have")]),
        (2, {"title": "B", "company": "Y"}, "new", 60, [gap("Kubernetes"), gap("Go"), gap("Docker", "adjacent", evidence="Administré cuentas en Active Directory")]),
        (3, {"title": "C", "company": "Z"}, "saved", 70, [gap("Kubernetes"), gap("Terraform"), gap("Python", "missing", confirmed=True)])]
    up = b.build_upskill(PROFILE, jobs, "es")
    kws = [i["keyword"] for i in up["items"]]
    assert kws[0] == "Kubernetes" and up["items"][0]["jobs_count"] == 3 and up["items"][0]["share_of_jobs"] == 1.0
    assert kws.index("Terraform") < kws.index("Go")
    assert "SIEM" not in kws and "Python" not in kws                         # lo que ya tienes o confirmaste no es una brecha
    docker = next(i for i in up["items"] if i["keyword"] == "Docker")
    assert docker["kind"] == "strengthen" and docker["profile_evidence"] and up["based_on_jobs"] == 3
    assert "confírmalo en tu perfil" in up["items"][0]["steps"][-1]
    assert b.build_upskill(PROFILE, jobs[:1], "es")["notes"][0].startswith("Pocas ofertas")


def test_llm_enrichment_is_validated():
    ok = {"questions": [{"question": "¿Cómo afinarías reglas en un SIEM?", "why": "stack"}, {"question": "Explica el stack", "why": "no es pregunta"},
                        {"question": "¿Reducirías 99% los falsos positivos?", "why": "cifra inventada"}]}
    out = b.enrich_with_llm(JOB_ES, ScriptedLLM([("entrevistador técnico", ok)]), "es")
    assert [q["question"] for q in out] == ["¿Cómo afinarías reglas en un SIEM?"] and out[0]["origin"] == "llm"
    from tests.cv_fakes import DownLLM
    assert b.enrich_with_llm(JOB_ES, DownLLM(), "es") == []
