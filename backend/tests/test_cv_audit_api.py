"""Integración: el auditor dentro de /tailor (con un redactor tramposo), y los endpoints de páginas y de verificación del archivo."""
import pytest

from app.api.deps import get_writer
from app.main import app
from tests.shared import API
from tests.test_cv_audit import PROFILE


class CheatingWriter:
    """Redactor que cumple con las palabras clave pero INVENTA: cifras, empresa, herramientas, un cargo y un logro."""

    def complete_json(self, system, user):
        return {"headline": "CISO en Telefónica | Analista SOC",
                "summary": "Analista SOC con 15 años en monitoreo SIEM y respuesta a incidentes, certificado CISSP.",
                "skills": ["SIEM", "Splunk", "Python", "Active Directory", "Terraform"],
                "experience": [{"bullets": ["Monitoreé alertas SIEM en Splunk las 24 horas", "Reduje 70% los costos de seguridad con Terraform en Google",
                                            "Respondí a incidentes de phishing y malware reduciendo el tiempo de contención en 30%",
                                            "Dirigí un equipo de 40 ingenieros en tres países"]},
                               {"bullets": ["Atendí 120 tickets mensuales de usuarios internos", "Administré cuentas en Active Directory y Office 365"]}]}


@pytest.fixture
def job(client, ready):
    assert client.post(f"{API}/me/searches", json={}, headers=ready).status_code == 202
    jid = next(j["id"] for j in client.get(f"{API}/me/jobs", headers=ready).json()["items"] if j["job"]["title"] == "Analista SOC")
    assert client.put(f"{API}/me/profile", json=PROFILE.model_dump(mode="json"), headers=ready).status_code == 200
    return ready, jid


def test_cheating_writer_is_neutralized_inside_tailor(client, job):
    auth, jid = job
    app.dependency_overrides[get_writer] = lambda: CheatingWriter()
    try:
        r = client.post(f"{API}/me/jobs/{jid}/tailor", json={}, headers=auth)
    finally:
        app.dependency_overrides.pop(get_writer, None)
    assert r.status_code == 200, r.text
    cv = r.json()["cv"]
    text = cv["text"]
    for invented in ("Telefónica", "CISSP", "Terraform", "Google", "70%", "40 ingenieros", "15 años"):
        assert invented not in text, invented
    assert cv["headline"] == PROFILE.headline + " | Analista SOC" or "CISO" not in cv["headline"]
    assert cv["audit"]["verdict"] == "ok" and cv["review_required"] is False and any(t.startswith("auditoría:") for t in cv["trace"])
    assert cv["audit"]["repaired"]
    # lo legítimo sobrevive
    assert "Monitoreé alertas SIEM en Splunk las 24 horas" in text and "Atendí 120 tickets" in text
    assert cv["artifact"]["ok"] and cv["ats"]["artifact_score"] == cv["artifact"]["artifact_score"]


def test_honest_tailor_is_not_flagged(client, job):
    auth, jid = job
    r = client.post(f"{API}/me/jobs/{jid}/tailor", json={}, headers=auth).json()["cv"]
    assert r["audit"]["verdict"] in ("ok", "warn") and r["review_required"] is False and r["artifact"]["artifact_score"] >= 1


def test_download_with_pages_and_reports(client, job):
    auth, jid = job
    cv_id = client.post(f"{API}/me/jobs/{jid}/tailor", json={}, headers=auth).json()["id"]
    p = client.get(f"{API}/me/cvs/{cv_id}.pdf?pages=1", headers=auth)
    assert p.status_code == 200 and p.content[:4] == b"%PDF" and p.headers["x-cv-pages"] == "1" and p.headers["x-cv-fits"] == "true"
    d = client.get(f"{API}/me/cvs/{cv_id}.docx?pages=2&template=compact", headers=auth)
    assert d.status_code == 200 and d.content[:2] == b"PK"
    fit = client.get(f"{API}/me/cvs/{cv_id}/fit?pages=1", headers=auth).json()
    assert fit["fits"] and fit["pages_by_template"].keys() == {"harvard", "classic", "compact", "modern"}
    art = client.get(f"{API}/me/cvs/{cv_id}/artifact?format=docx&template=modern", headers=auth).json()
    assert art["ok"] and art["format"] == "docx" and "artifact_score" in art
    assert client.get(f"{API}/me/cvs/{cv_id}.pdf?pages=3", headers=auth).status_code == 422
    assert client.get(f"{API}/me/cvs/{cv_id}/artifact?template=x", headers=auth).status_code == 422


def test_other_user_cannot_read_reports(client, job):
    from tests.conftest import register
    auth, jid = job
    cv_id = client.post(f"{API}/me/jobs/{jid}/tailor", json={}, headers=auth).json()["id"]
    other = register(client, "otra@x.com")
    for path in (f"{cv_id}/fit", f"{cv_id}/artifact", f"{cv_id}.pdf?pages=1"):
        assert client.get(f"{API}/me/cvs/{path}", headers=other).status_code == 404


def test_cover_letter_is_fitted_to_one_page(client, job):
    auth, jid = job
    r = client.post(f"{API}/me/jobs/{jid}/cover-letter", json={}, headers=auth).json()
    assert r["fit"]["pages_after"] == 1 and r["fit"]["fits"] and r["text"]
