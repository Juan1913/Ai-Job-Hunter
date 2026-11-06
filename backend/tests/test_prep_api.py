"""Preparación como tarea en segundo plano: caché por huella, trazabilidad al perfil, idioma, rango salarial y plan de mejora global."""
import pytest

from app.db import PrepRow
from tests.shared import API
from tests.test_cv_audit import PROFILE


@pytest.fixture
def job(client, ready):
    assert client.post(f"{API}/me/searches", json={}, headers=ready).status_code == 202
    jid = next(j["id"] for j in client.get(f"{API}/me/jobs", headers=ready).json()["items"] if j["job"]["title"] == "Analista SOC")
    assert client.put(f"{API}/me/profile", json=PROFILE.model_dump(mode="json"), headers=ready).status_code == 200      # perfil con experiencia real
    return ready, jid


def _task(client, auth, jid, **body):
    r = client.post(f"{API}/me/jobs/{jid}/prep", json=body, headers=auth)
    assert r.status_code == 202, r.text
    t = client.get(f"{API}/me/tasks/{r.json()['id']}", headers=auth).json()
    assert t["status"] == "done", t
    return t


def test_prep_task_builds_and_stores_everything_with_traceability(client, job):
    auth, jid = job
    t = _task(client, auth, jid)
    assert set(t["result"]["parts"]) == {"stories", "questions", "ask", "negotiation", "upskill"} and not any(p["cached"] for p in t["result"]["parts"].values())
    p = client.get(f"{API}/me/jobs/{jid}/prep", headers=auth).json()
    assert p["ready"] == ["ask", "negotiation", "questions", "stories", "upskill"] and not any(p["stale"].values())
    assert p["stories"]["lang"] == "es" and p["stories"]["stories"]
    for s in p["stories"]["stories"]:
        assert s["source"]["ref"].startswith("experience:") and s["audit"]["status"] != "unsupported"
    assert any(q["type"] == "gap" for q in p["questions"]["honest_gaps"]) or p["questions"]["honest_gaps"]
    assert p["negotiation"]["unknown"] and p["ask"]["ask"]
    assert p["upskill"]["based_on_jobs"] >= 1          # el análisis se ejecutó solo, porque las brechas salen de él


def test_second_run_is_cached_and_force_recomputes(client, job):
    auth, jid = job
    _task(client, auth, jid)
    t2 = _task(client, auth, jid)
    assert all(p["cached"] for p in t2["result"]["parts"].values())
    t3 = _task(client, auth, jid, force=True)
    assert not any(p["cached"] for p in t3["result"]["parts"].values())


def test_salary_range_recomputes_only_negotiation_and_is_remembered(client, job):
    auth, jid = job
    _task(client, auth, jid)
    t = _task(client, auth, jid, salary_min=5000000, salary_max=7000000, salary_currency="COP")
    parts = t["result"]["parts"]
    assert parts["negotiation"]["cached"] is False and parts["stories"]["cached"] is True and parts["ask"]["cached"] is True
    neg = client.get(f"{API}/me/jobs/{jid}/prep", headers=auth).json()["negotiation"]
    assert neg["user_range"] == {"min": 5000000.0, "max": 7000000.0, "currency": "COP"}
    app = client.get(f"{API}/me/jobs/{jid}/application", headers=auth).json()
    assert app["expected_salary"]["min"] == 5000000          # queda guardado en la candidatura
    assert client.post(f"{API}/me/jobs/{jid}/prep", json={"salary_min": 9, "salary_max": 3}, headers=auth).status_code == 422


def test_profile_change_marks_prep_stale(client, job):
    auth, jid = job
    _task(client, auth, jid, parts=["stories"])
    prof = client.get(f"{API}/me/profile", headers=auth).json()
    prof["experience"][0]["bullets"].append("Implementé reglas de correlación nuevas en Splunk")
    assert client.put(f"{API}/me/profile", json=prof, headers=auth).status_code == 200
    assert client.get(f"{API}/me/jobs/{jid}/prep", headers=auth).json()["stale"]["stories"] is True
    t = _task(client, auth, jid, parts=["stories"])
    assert t["result"]["parts"]["stories"]["cached"] is False


def test_english_override_and_partial_parts(client, job):
    auth, jid = job
    _task(client, auth, jid, parts=["ask"], lang="en")
    p = client.get(f"{API}/me/jobs/{jid}/prep", headers=auth).json()
    assert p["ready"] == ["ask"] and p["ask"]["lang"] == "en" and "success look like" in p["ask"]["ask"][0]["question"]


def test_generic_task_route_accepts_prep_kind(client, job):
    auth, jid = job
    r = client.post(f"{API}/me/jobs/{jid}/tasks", json={"kind": "prep", "lang": "es"}, headers=auth)
    assert r.status_code == 202
    assert client.get(f"{API}/me/tasks/{r.json()['id']}", headers=auth).json()["status"] == "done"
    assert client.post(f"{API}/me/jobs/{jid}/tasks", json={"kind": "inexistente"}, headers=auth).status_code == 422


def test_global_upskill_endpoint(client, job):
    auth, jid = job
    client.post(f"{API}/me/jobs/{jid}/analyze", headers=auth)
    up = client.get(f"{API}/me/prep/upskill", headers=auth).json()
    assert up["based_on_jobs"] >= 1 and all(i["steps"] and i["jobs_count"] >= 1 for i in up["items"])
    assert client.get(f"{API}/me/prep/upskill?lang=en", headers=auth).json()["notes"][-1].startswith("Priority")


def test_prep_rows_are_one_per_kind(client, job):
    from sqlmodel import Session, select

    from app.db import engine
    auth, jid = job
    _task(client, auth, jid)
    _task(client, auth, jid, force=True)
    with Session(engine) as s:
        assert len(s.exec(select(PrepRow)).all()) == 5
