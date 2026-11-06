"""CRUD de búsquedas programadas y reglas de cortesía (intervalo mínimo, límites, aislamiento entre usuarios)."""
from app.config import settings
from tests.conftest import register
from tests.shared import API

URL = f"{API}/me/saved-searches"


def _create(client, h, **kw):
    body = {"name": "SOC Colombia", "keywords": ["analista soc"], "interval_minutes": 360, **kw}
    return client.post(URL, json=body, headers=h)


def test_create_list_get_patch_delete(client, auth):
    r = _create(client, auth, window_start="07:00", window_end="20:00", timezone="America/Bogota", countries=["co"])
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["active"] and s["params"]["keywords"] == ["analista soc"] and s["params"]["countries"] == ["CO"] and s["next_run_at"]
    assert client.get(URL, headers=auth).json()[0]["id"] == s["id"]
    p = client.patch(f"{URL}/{s['id']}", json={"interval_minutes": 720, "keywords": None, "hours_old": 48}, headers=auth).json()
    assert p["interval_minutes"] == 720 and "keywords" not in p["params"] and p["params"]["hours_old"] == 48
    assert client.delete(f"{URL}/{s['id']}", headers=auth).status_code == 204
    assert client.get(f"{URL}/{s['id']}", headers=auth).status_code == 404


def test_minimum_interval_window_and_count_limits(client, auth, monkeypatch):
    assert _create(client, auth, interval_minutes=5).status_code == 422                        # < mínimo configurable (60)
    monkeypatch.setattr(settings, "schedule_min_interval_min", 5)
    assert _create(client, auth, name="a", interval_minutes=5).status_code == 201
    assert _create(client, auth, name="otra", timezone="Marte/Olimpo").status_code == 422
    assert _create(client, auth, name="otra", window_start="07:00").status_code == 422        # ventana a medias
    assert _create(client, auth, name="otra", window_start="7am", window_end="8pm").status_code == 422
    assert _create(client, auth).status_code == 201
    assert _create(client, auth).status_code == 409                                            # mismo nombre
    monkeypatch.setattr(settings, "schedule_max_per_user", 2)
    assert _create(client, auth, name="tercera").status_code == 409


def test_pause_resume_resets_failures_and_run_now_rules(client, auth):
    sid = _create(client, auth).json()["id"]
    assert client.post(f"{URL}/{sid}/pause", headers=auth).json()["active"] is False
    assert client.post(f"{URL}/{sid}/run", headers=auth).status_code == 409                   # pausada
    from sqlmodel import Session

    from app.db import SavedSearch, engine
    with Session(engine) as s:
        ss = s.get(SavedSearch, sid)
        ss.failure_streak, ss.last_error = 4, "x"
        s.add(ss)
        s.commit()
    r = client.post(f"{URL}/{sid}/resume", headers=auth).json()
    assert r["active"] and r["failure_streak"] == 0 and r["paused_reason"] is None
    assert client.post(f"{URL}/{sid}/run", headers=auth).status_code == 202


def test_saved_searches_are_private_per_user(client, auth):
    sid = _create(client, auth).json()["id"]
    other = register(client, "otra@x.com")
    assert client.get(URL, headers=other).json() == []
    for call in (lambda: client.get(f"{URL}/{sid}", headers=other), lambda: client.patch(f"{URL}/{sid}", json={"name": "x"}, headers=other),
                 lambda: client.delete(f"{URL}/{sid}", headers=other), lambda: client.post(f"{URL}/{sid}/run", headers=other),
                 lambda: client.post(f"{URL}/{sid}/pause", headers=other)):
        assert call().status_code == 404
    assert _create(client, other).status_code == 201                                           # mismo nombre en otro usuario: permitido
    assert client.get(URL, headers=auth).json()[0]["id"] == sid


def test_requires_auth(client):
    assert client.get(URL).status_code == 401
