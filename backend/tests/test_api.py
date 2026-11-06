from tests.conftest import register
from tests.shared import API, CV, JOBS  # noqa: F401


def test_auth_required_and_validation(client):
    assert client.get(f"{API}/me/profile").status_code == 401
    assert client.get(f"{API}/me/profile", headers={"Authorization": "Bearer basura"}).status_code == 401
    assert client.post(f"{API}/auth/register", json={"email": "a@x.com", "password": "corta"}).status_code == 422
    assert client.post(f"{API}/auth/register", json={"email": "no-es-email", "password": "una-clave-larga-123"}).status_code == 422
    register(client, "a@x.com")
    assert client.post(f"{API}/auth/register", json={"email": "A@x.com", "password": "una-clave-larga-123"}).status_code == 409
    assert client.post(f"{API}/auth/login", json={"email": "a@x.com", "password": "incorrecta-123456"}).status_code == 401
    assert client.post(f"{API}/auth/login", json={"email": "a@x.com", "password": "una-clave-larga-123"}).status_code == 200


def test_profile_flow_and_reupload_keeps_preferences(client, ready):
    assert client.get(f"{API}/me/profile", headers=ready).json()["preferences"]["countries"] == ["CO"]
    client.post(f"{API}/me/skills/confirm", json={"name": "AWS"}, headers=ready)
    p = client.post(f"{API}/me/cv", files=CV, headers=ready).json()
    assert p["preferences"]["target_roles"] == ["analista soc"]
    assert any(s["name"] == "AWS" and s["source"] == "confirmed" for s in p["skills"])


def test_cv_upload_limits(client, auth):
    assert client.post(f"{API}/me/cv", files={"file": ("x.exe", b"MZ" * 50)}, headers=auth).status_code == 415
    assert client.post(f"{API}/me/cv", files={"file": ("x.txt", b"corto")}, headers=auth).status_code == 422
    assert client.post(f"{API}/me/cv", files={"file": ("x.pdf", b"no es un pdf" * 20)}, headers=auth).status_code == 422


def test_search_persists_dedups_and_reports_per_source(client, ready):
    r = client.post(f"{API}/me/searches", json={}, headers=ready)
    assert r.status_code == 202
    run = client.get(f"{API}/me/searches/{r.json()['id']}", headers=ready).json()
    assert run["status"] == "done" and run["new_jobs"] == 2
    assert run["sources"]["ok"]["status"] == "done" and run["sources"]["roto"]["status"] == "error"
    again = client.post(f"{API}/me/searches", json={}, headers=ready).json()
    assert again["new_jobs"] == 0                                           # misma oferta no se duplica
    page = client.get(f"{API}/me/jobs", headers=ready).json()
    assert page["total"] == 2 and page["items"][0]["job"]["title"] == "Analista SOC"   # ordenado por relevancia
    assert page["items"][0]["stage1"] > page["items"][1]["stage1"]


def test_users_are_isolated(client, ready):
    client.post(f"{API}/me/searches", json={}, headers=ready)
    jid = client.get(f"{API}/me/jobs", headers=ready).json()["items"][0]["id"]
    other = register(client, "otra@x.com")
    assert client.get(f"{API}/me/jobs", headers=other).json()["total"] == 0
    for method, path in [("get", f"/me/jobs/{jid}"), ("post", f"/me/jobs/{jid}/analyze"), ("post", f"/me/jobs/{jid}/apply"),
                         ("get", f"/me/jobs/{jid}/events")]:
        assert getattr(client, method)(API + path, headers=other).status_code == 404
    assert client.patch(f"{API}/me/jobs/{jid}", json={"status": "saved"}, headers=other).status_code == 404


def test_analyze_tailor_apply_lifecycle(client, ready, llm):
    client.post(f"{API}/me/searches", json={}, headers=ready)
    jid = client.get(f"{API}/me/jobs", headers=ready).json()["items"][0]["id"]
    a = client.post(f"{API}/me/jobs/{jid}/analyze", headers=ready).json()
    assert a["match"] > 0 and {g["keyword"]: g["status"] for g in a["gaps"]}["AWS"] == "missing"
    llm.bad_first = True
    t = client.post(f"{API}/me/jobs/{jid}/tailor", json={}, headers=ready).json()
    assert "AWS" not in t["cv"]["text"] and t["cv"]["ats"]["score"] >= 70      # el harness corrigió la fuga
    t2 = client.post(f"{API}/me/jobs/{jid}/tailor", json={"confirmed": ["AWS"]}, headers=ready).json()
    assert "AWS" in t2["cv"]["text"]
    assert len(client.get(f"{API}/me/jobs/{jid}/cvs", headers=ready).json()) == 2
    d = client.get(f"{API}/me/cvs/{t2['id']}.docx", headers=ready)
    assert d.status_code == 200 and d.content[:2] == b"PK"
    ap = client.post(f"{API}/me/jobs/{jid}/apply", headers=ready).json()
    assert ap == {"url": "https://ats.b/apply/1", "external": True}
    client.patch(f"{API}/me/jobs/{jid}", json={"status": "applied"}, headers=ready)
    kinds = [e["kind"] for e in client.get(f"{API}/me/jobs/{jid}/events", headers=ready).json()]
    assert kinds == ["discovered", "analyzed", "tailored", "tailored", "apply_opened", "status:applied"]
    assert client.get(f"{API}/me/stats", headers=ready).json()["by_status"]["applied"] == 1


def test_discarded_hidden_by_default(client, ready):
    client.post(f"{API}/me/searches", json={}, headers=ready)
    jid = client.get(f"{API}/me/jobs", headers=ready).json()["items"][0]["id"]
    client.patch(f"{API}/me/jobs/{jid}", json={"status": "discarded"}, headers=ready)
    assert client.get(f"{API}/me/jobs", headers=ready).json()["total"] == 1
    assert client.get(f"{API}/me/jobs?status=discarded", headers=ready).json()["total"] == 1


def test_company_registration_blocks_ssrf_and_detects_ats(client, auth, monkeypatch):
    for bad in ["http://127.0.0.1/admin", "http://localhost:8000", "http://169.254.169.254/latest/meta-data", "file:///etc/passwd", "ftp://x.com"]:
        assert client.post(f"{API}/me/companies", json={"name": "x", "url": bad}, headers=auth).status_code == 422, bad
    monkeypatch.setattr("app.scout.netguard.socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    c = client.post(f"{API}/me/companies", json={"name": "Mercado", "url": "https://boards.greenhouse.io/mercadolibre"}, headers=auth).json()
    assert (c["kind"], c["token"]) == ("greenhouse", "mercadolibre")
    assert client.post(f"{API}/me/companies", json={"name": "Otro", "url": "https://empresa.com/careers"}, headers=auth).json()["kind"] == "careers"
    assert len(client.get(f"{API}/me/companies", headers=auth).json()) == 2
    assert client.delete(f"{API}/me/companies/{c['id']}", headers=auth).status_code == 204
