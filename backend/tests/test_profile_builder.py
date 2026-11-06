from pathlib import Path

import pytest

from app.llm.client import LLMError
from app.profile.builder import build_profile, build_profile_with_report
from app.profile.extract import extract_document
from app.text import norm
from tests.cv_fakes import BrokenJSONLLM, DownLLM, ScriptedLLM
from tests.fakes import FakeLLM
from tests.fixtures_cv.data import CVS, VARIANTS
from tests.fixtures_cv.messy import MESSY

FIX = Path(__file__).parent / "fixtures_cv"
READABLE = [v for v in VARIANTS if v[3] != "scan"]

def _digits(s):
    return "".join(c for c in (s or "") if c.isdigit())

@pytest.mark.parametrize("name,key,fmt,layout", READABLE, ids=[r[0] for r in READABLE])
def test_real_layouts_build_full_profile_without_llm(name, key, fmt, layout):
    """Con el LLM caído el usuario igual obtiene un perfil completo y editable (fallback determinista)."""
    cv = CVS[key]
    ex = extract_document(f"{name}.{fmt}", (FIX / f"{name}.{fmt}").read_bytes())
    res = build_profile_with_report(ex.text, DownLLM(), ex.method)
    p, r = res.profile, res.report
    assert p.full_name == cv["name"] and p.email == cv["truth"]["email"] and _digits(p.phone) == _digits(cv["truth"]["phone"])
    assert p.headline == cv["headline"]
    assert [e.company for e in p.experience] == [e[0] for e in cv["experience"]]
    assert [e.title for e in p.experience] == [e[1] for e in cv["experience"]]
    assert all(len(e.bullets) == len(src[4]) for e, src in zip(p.experience, cv["experience"]))
    assert p.experience[0].end is None and p.experience[0].start and p.experience[1].end        # 'Actualidad/Present' -> None
    assert {norm(s.name) for s in p.skills} >= {norm(s) for s in cv["skills"]}
    assert len(p.education) == len(cv["education"]) and p.education[0].year == cv["education"][0][2]
    assert len(p.languages) == len(cv["languages"]) and len(p.certifications) == len(cv["certs"])
    assert r.overall >= 0.8 and r.needs_review == [] and not r.dropped
    assert r.fields["email"].source == "regex" and r.language == cv["lang"]

@pytest.mark.parametrize("key", list(MESSY))
def test_messy_cvs_ground_truth(key):
    case = MESSY[key]
    t = case["truth"]
    res = build_profile_with_report(case["text"], DownLLM())
    p = res.profile
    assert p.email == t["email"] and _digits(p.phone) == _digits(t["phone"])
    if "name" in t:
        assert p.full_name == t["name"]
    assert len(p.experience) == t["n_exp"]
    assert [e.company for e in p.experience] == t["companies"]
    if "titles" in t:
        assert [e.title for e in p.experience] == t["titles"]
    got_sk = {norm(s.name) for s in p.skills}
    assert all(norm(s) in got_sk for s in t.get("skills", []))
    blob = norm(str(p.model_dump()))
    assert not any(norm(a) in blob for a in case["absent"])

def test_dates_are_normalized_and_current_job_has_no_end():
    p = build_profile(MESSY["gerente_tienda_es"]["text"], DownLLM())
    assert (p.experience[0].start, p.experience[0].end) == ("2019-01", None)
    assert (p.experience[1].start, p.experience[1].end) == ("2013-03", "2018-12")
    assert p.languages == ["Español (nativo)", "Inglés (C1)", "Francés (B1)"]

def test_down_llm_is_called_once_not_per_section_and_report_says_so():
    llm = DownLLM()
    res = build_profile_with_report(MESSY["sin_secciones_en"]["text"], llm)
    assert llm.calls == 1                                        # cortacircuito: no repite timeouts de 60 s por sección
    assert res.report.llm_failures >= 1 and any("no respondió" in w for w in res.report.warnings)
    assert res.profile.email and res.profile.experience          # perfil editable aunque el modelo caiga
    assert "skills" in res.report.needs_review                   # y se avisa de lo que falta

def test_llm_hallucinations_are_dropped_and_reported():
    text = "Documento sin estructura\nsofia@x.com +1 305 555 0199\nTrabajo en marketing digital con SEO y email marketing desde hace años y gestiono campañas de pago.\n"
    llm = ScriptedLLM([
        ("Del texto lista las habilidades", {"skills": ["SEO", "email marketing", "Kubernetes", "Terraform", "campañas de pago"]}),
        ("primeras líneas de un CV", {"full_name": "John Inventado", "headline": "CTO en Google", "location": "Narnia"}),
        ("extrae la experiencia laboral", {"experience": [{"title": "CTO", "company": "Google", "start": "2010", "end": None}]}),
    ])
    res = build_profile_with_report(text, llm)
    names = {s.name for s in res.profile.skills}
    assert {"SEO", "email marketing"} <= names and not names & {"Kubernetes", "Terraform"}
    assert res.profile.full_name == "" and not res.profile.experience and res.profile.location is None
    d = " ".join(res.report.dropped)
    assert "Kubernetes" in d and "John Inventado" in d and "Google" in d and "Narnia" in d
    assert "full_name" in res.report.needs_review and "experience" in res.report.needs_review

AMBIG = """Rocío Valdés
rocio@x.com +34 600 111 222

EXPERIENCIA
Nimbus Quantum
Chief Wizard
2020 - 2022
- Hechizos.
"""

def test_llm_resolves_ambiguous_header_but_cannot_invent():
    ok = ScriptedLLM([("cabeceras de puestos", {"entries": [{"n": 1, "title": "Chief Wizard", "company": "Nimbus Quantum"}]})])
    e = build_profile(AMBIG, ok).experience[0]
    assert (e.title, e.company) == ("Chief Wizard", "Nimbus Quantum")
    bad = ScriptedLLM([("cabeceras de puestos", {"entries": [{"n": 1, "title": "Gerente", "company": "Google"}]})])
    res = build_profile_with_report(AMBIG, bad)
    assert res.profile.experience[0].company != "Google" and res.profile.experience[0].title != "Gerente"
    assert any("Google" in d for d in res.report.dropped)

def test_broken_json_is_repaired_from_raw_output():
    llm = BrokenJSONLLM(raw=["Aquí tienes: {'entries': [{'n': 1, 'title': 'Chief Wizard', 'company': 'Nimbus Quantum',},]}"])
    res = build_profile_with_report(AMBIG, llm)
    e = res.profile.experience[0]
    assert (e.title, e.company) == ("Chief Wizard", "Nimbus Quantum")

def test_garbage_llm_output_retries_then_falls_back():
    llm = ScriptedLLM([("cabeceras de puestos", [LLMError("JSON inválido del modelo: x")])], raw=["esto no es json"])
    res = build_profile_with_report(AMBIG, llm)
    assert res.profile.experience and res.report.llm_failures >= 1
    assert res.profile.email == "rocio@x.com"

def test_email_and_phone_come_from_regex_never_from_the_model():
    llm = ScriptedLLM([("primeras líneas", {"full_name": "Rocío Valdés", "email": "falso@evil.com", "phone": "000"})])
    p = build_profile(AMBIG, llm)
    assert p.email == "rocio@x.com" and p.phone == "+34 600 111 222"

def test_full_profile_dict_from_legacy_fake_llm_is_still_verified():
    """FakeLLM de la suite devuelve un perfil entero para cualquier prompt: lo no presente en el texto se descarta."""
    text = ("Luis Gomez analista de ciberseguridad con experiencia en SIEM y respuesta a incidentes " * 3)
    res = build_profile_with_report(text, FakeLLM())
    names = {s.name for s in res.profile.skills}
    assert "SIEM" in names and "Splunk" not in names and "Python" not in names
    assert not res.profile.experience and res.profile.email is None and res.profile.full_name == "Luis Gómez"

def test_empty_and_tiny_inputs_do_not_crash():
    for t in ("", "   ", "hola"):
        res = build_profile_with_report(t, DownLLM())
        assert res.profile.skills == [] and res.report.overall < 0.3

def test_report_is_json_serializable_with_per_entry_quality():
    res = build_profile_with_report(MESSY["gerente_tienda_es"]["text"], DownLLM())
    data = res.report.model_dump(mode="json")
    assert set(data["fields"]) >= {"full_name", "email", "phone", "skills", "experience", "education"}
    assert data["entries"]["experience"][0]["company"] == "Zara España" and 0 < data["overall"] <= 1
