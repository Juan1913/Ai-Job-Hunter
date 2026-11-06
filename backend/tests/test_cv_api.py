import io
from pathlib import Path

import pytest
from docx import Document

from app.api.deps import get_llm, get_writer
from app.domain.job import Job
from app.main import app
from app.profile.extract import extract_document
from tests.conftest import register
from tests.cv_fakes import ScriptedLLM

API = "/api/v1"
FIX = Path(__file__).parent / "fixtures_cv"

class Stub:
    countries: set = set()

    def __init__(self, jobs):
        self.name, self._jobs = "ok", jobs

    def search(self, q):
        return self._jobs

JOBS = [Job(title="Ejecutivo Comercial", company="Rappi", location="Medellín", url="https://r/1", source="a",
            description="Buscamos un ejecutivo comercial con experiencia en venta consultiva, Salesforce, HubSpot y SAP CRM. Ofrecemos contrato indefinido en un equipo con muchas ganas.")]

def _upload(client, auth, name):
    return client.post(f"{API}/me/cv", files={"file": (name, (FIX / name).read_bytes())}, headers=auth)

@pytest.fixture
def ready(client, auth, monkeypatch):
    monkeypatch.setattr("app.services.runner.build_sources", lambda c=None: [Stub(JOBS)])
    assert _upload(client, auth, "ventas_es_2col.pdf").status_code == 200
    client.put(f"{API}/me/preferences", json={"countries": ["CO"], "target_roles": ["ejecutivo comercial"], "languages": ["es"]}, headers=auth)
    client.post(f"{API}/me/searches", json={}, headers=auth)
    jid = client.get(f"{API}/me/jobs", headers=auth).json()["items"][0]["id"]
    return auth, jid

def test_upload_real_two_column_pdf_builds_profile_and_exposes_quality(client, auth):
    r = _upload(client, auth, "ventas_es_2col.pdf")
    assert r.status_code == 200
    p = r.json()
    assert p["full_name"] == "Carolina Ruiz Medina" and p["email"] == "carolina.ruiz@correo.co" and len(p["experience"]) == 3
    assert float(r.headers["X-Profile-Quality"]) >= 0.8 and r.headers["X-Profile-Review"] == ""

def test_preview_returns_report_without_saving(client, auth):
    r = client.post(f"{API}/me/cv/preview", files={"file": ("cv.docx", (FIX / "enfermeria_es_tabla.docx").read_bytes())}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["profile"]["full_name"] == "María Fernanda López"
    assert body["report"]["fields"]["email"]["source"] == "regex" and body["report"]["needs_review"] == []
    assert not client.get(f"{API}/me/profile", headers=auth).json().get("full_name")        # no se guardó nada

def test_scanned_pdf_gets_actionable_message(client, auth):
    r = _upload(client, auth, "ventas_es_escaneo.pdf")
    assert r.status_code == 422 and "escaneo" in r.json()["detail"]

def test_corrupt_files_give_422_with_reason(client, auth):
    r = client.post(f"{API}/me/cv", files={"file": ("x.pdf", b"%PDF-1.4 roto" * 20)}, headers=auth)
    assert r.status_code == 422 and "PDF" in r.json()["detail"]

def test_pdf_and_docx_templates_download(client, ready):
    auth, jid = ready
    t = client.post(f"{API}/me/jobs/{jid}/tailor", json={}, headers=auth).json()
    assert t["cv"]["lang"] == "es" and t["cv"]["ats"]["breakdown"]
    cid = t["id"]
    for tpl in ("harvard", "classic", "compact", "modern"):
        pdf = client.get(f"{API}/me/cvs/{cid}.pdf?template={tpl}", headers=auth)
        assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content[:4] == b"%PDF"
        text = extract_document("x.pdf", pdf.content).text
        assert "CAROLINA RUIZ MEDINA" in text and "EXPERIENCIA LABORAL" in text and "carolina.ruiz@correo.co" in text
        dx = client.get(f"{API}/me/cvs/{cid}.docx?template={tpl}", headers=auth)
        assert dx.status_code == 200 and dx.content[:2] == b"PK"
        assert "EXPERIENCIA LABORAL" in [p.text for p in Document(io.BytesIO(dx.content)).paragraphs]
    assert client.get(f"{API}/me/cvs/{cid}.docx", headers=auth).status_code == 200       # sin ?template= sigue funcionando
    assert client.get(f"{API}/me/cvs/{cid}.pdf?template=neon", headers=auth).status_code == 422
    other = register(client, "otro@x.com")
    assert client.get(f"{API}/me/cvs/{cid}.pdf", headers=other).status_code == 404

def test_cover_letter_endpoint_guards_and_isolation(client, ready, llm):
    auth, jid = ready
    clean = ("Estimado equipo de Rappi:\n\nMe dirijo a ustedes para postularme al puesto de Ejecutivo Comercial. Soy ejecutiva comercial B2B con 8 años de experiencia en venta "
             "consultiva de software y servicios, y en Siigo gestioné un portafolio de cuentas corporativas con ventas anuales importantes para la compañía.\n\nTengo experiencia "
             "con Salesforce y HubSpot, y supe aumentar la tasa de cierre con un proceso de calificación claro. Disfruto trabajar con equipos comerciales y de producto para "
             "entender las necesidades del cliente y proponer soluciones concretas que generen resultados medibles.\n\nAgradezco su tiempo y quedo atenta a poder conversar "
             "sobre cómo puedo aportar al equipo.\n\nAtentamente,\nCarolina Ruiz Medina")
    app.dependency_overrides[get_llm] = lambda: ScriptedLLM([("Extrae de una oferta", {"keywords": ["Salesforce", "HubSpot", "SAP CRM"]}), ("adyacentes", {"adjacent": []})])
    app.dependency_overrides[get_writer] = lambda: ScriptedLLM([("redactor de cartas", {"letter": clean})])
    r = client.post(f"{API}/me/jobs/{jid}/cover-letter", json={}, headers=auth)
    assert r.status_code == 200 and r.json()["source"] == "llm" and r.json()["lang"] == "es" and r.json()["text"].endswith("Carolina Ruiz Medina")
    cheat = clean.replace("Tengo experiencia", "Domino SAP CRM. Tengo experiencia")
    app.dependency_overrides[get_writer] = lambda: ScriptedLLM([("redactor de cartas", {"letter": cheat})])
    r2 = client.post(f"{API}/me/jobs/{jid}/cover-letter", json={}, headers=auth).json()
    assert "SAP CRM" not in r2["text"]                                           # no confirmado: se elimina
    r3 = client.post(f"{API}/me/jobs/{jid}/cover-letter", json={"confirmed": ["SAP CRM"]}, headers=auth).json()
    assert "SAP CRM" in r3["text"]                                               # confirmado por el usuario: permitido
    other = register(client, "otro2@x.com")
    assert client.post(f"{API}/me/jobs/{jid}/cover-letter", json={}, headers=other).status_code == 404
    assert client.post(f"{API}/me/jobs/{jid}/cover-letter", json={}).status_code == 401

def test_cover_letter_degrades_to_template_when_writer_is_useless(client, ready):
    auth, jid = ready
    r = client.post(f"{API}/me/jobs/{jid}/cover-letter", json={}, headers=auth).json()      # FakeLLM no sabe escribir cartas
    assert r["source"] == "template" and r["text"].startswith("Estimado equipo de Rappi") and "Carolina Ruiz Medina" in r["text"]

def test_english_job_gets_english_cv(client, auth, monkeypatch):
    en = [Job(title="Sales Executive", company="Acme", location="Remote", url="https://a/1", source="a",
              description="We are looking for a sales executive with Salesforce and HubSpot experience. You will join our team and work with the company's customers.")]
    monkeypatch.setattr("app.services.runner.build_sources", lambda c=None: [Stub(en)])
    _upload(client, auth, "ventas_es_2col.pdf")
    client.put(f"{API}/me/preferences", json={"countries": ["CO"], "target_roles": ["sales executive"], "languages": ["en"]}, headers=auth)
    client.post(f"{API}/me/searches", json={}, headers=auth)
    jid = client.get(f"{API}/me/jobs", headers=auth).json()["items"][0]["id"]
    t = client.post(f"{API}/me/jobs/{jid}/tailor", json={}, headers=auth).json()
    assert t["cv"]["lang"] == "en" and "WORK EXPERIENCE" in t["cv"]["text"] and "EXPERIENCIA" not in t["cv"]["text"]
    assert "Mar 2021 - Present" in t["cv"]["text"] and t["cv"]["ats"]["lang"] == "en"
