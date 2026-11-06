from tests.conftest import register
from tests.shared import API


def _first_job(client, h):
    client.post(f"{API}/me/searches", json={}, headers=h)
    return client.get(f"{API}/me/jobs", headers=h).json()["items"][0]["id"]


def test_analyze_and_tailor_run_as_background_tasks(client, ready, llm):
    jid = _first_job(client, ready)
    t = client.post(f"{API}/me/jobs/{jid}/tasks", json={"kind": "analyze"}, headers=ready)
    assert t.status_code == 202
    done = client.get(f"{API}/me/tasks/{t.json()['id']}", headers=ready).json()
    assert done["status"] == "done" and done["result"]["match"] > 0
    t2 = client.post(f"{API}/me/jobs/{jid}/tasks", json={"kind": "tailor", "confirmed": ["AWS"]}, headers=ready).json()
    res = client.get(f"{API}/me/tasks/{t2['id']}", headers=ready).json()
    assert res["status"] == "done" and "AWS" in res["result"]["cv"]["text"]
    assert len(client.get(f"{API}/me/tasks", headers=ready).json()) == 2


def test_task_failure_is_reported_not_raised(client, ready):
    jid = _first_job(client, ready)
    client.put(f"{API}/me/profile", json={"full_name": ""}, headers=ready)   # perfil vacío -> analyze/tailor siguen, pero kind malo falla en validación
    assert client.post(f"{API}/me/jobs/{jid}/tasks", json={"kind": "nada"}, headers=ready).status_code == 422


def test_tasks_are_private_and_validated(client, ready):
    jid = _first_job(client, ready)
    tid = client.post(f"{API}/me/jobs/{jid}/tasks", json={"kind": "analyze"}, headers=ready).json()["id"]
    other = register(client, "otra@x.com")
    assert client.get(f"{API}/me/tasks/{tid}", headers=other).status_code == 404
    assert client.post(f"{API}/me/jobs/{jid}/tasks", json={"kind": "analyze"}, headers=other).status_code == 404
    assert client.get(f"{API}/me/tasks", headers=other).json() == []


def test_task_respects_daily_quota(client, ready, monkeypatch):
    from app.config import settings
    jid = _first_job(client, ready)
    monkeypatch.setattr(settings, "quota_analyze_per_day", 1)
    assert client.post(f"{API}/me/jobs/{jid}/tasks", json={"kind": "analyze", "force": True}, headers=ready).status_code == 202
    assert client.post(f"{API}/me/jobs/{jid}/tasks", json={"kind": "analyze", "force": True}, headers=ready).status_code == 429
