import pytest
from sqlalchemy import text
from sqlmodel import Session

from app.config import settings
from app.db import engine
from app.middleware import rate_limiter
from tests.conftest import register
from tests.shared import CV, JOBS, StubSource

API = "/api/v1"
PW = "una-clave-larga-123"


@pytest.fixture(autouse=True)
def _clean():
    rate_limiter.reset()


def _login(client, email="luis@x.com", pw=PW):
    r = client.post(f"{API}/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()


def test_register_returns_access_and_refresh(client):
    r = client.post(f"{API}/auth/register", json={"email": "luis@x.com", "password": PW}).json()
    assert r["access_token"] and r["refresh_token"] and r["expires_in"] == settings.jwt_ttl_minutes * 60


def test_refresh_rotates_and_reuse_revokes_family(client):
    t0 = client.post(f"{API}/auth/register", json={"email": "luis@x.com", "password": PW}).json()
    r1 = client.post(f"{API}/auth/refresh", json={"refresh_token": t0["refresh_token"]})
    assert r1.status_code == 200
    t1 = r1.json()
    assert t1["refresh_token"] != t0["refresh_token"]
    assert client.get(f"{API}/me", headers={"Authorization": f"Bearer {t1['access_token']}"}).status_code == 200
    # reutilizar el token ya rotado = posible robo: falla y mata también al token nuevo
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": t0["refresh_token"]}).status_code == 401
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": t1["refresh_token"]}).status_code == 401
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": "inventado"}).status_code == 401


def test_refresh_expired_and_logout(client):
    t = client.post(f"{API}/auth/register", json={"email": "luis@x.com", "password": PW}).json()
    with Session(engine) as s:
        s.execute(text("UPDATE refreshtoken SET expires_at='2000-01-01 00:00:00'"))
        s.commit()
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": t["refresh_token"]}).status_code == 401
    t2 = _login(client)
    assert client.post(f"{API}/auth/logout", json={"refresh_token": t2["refresh_token"]}).status_code == 204
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": t2["refresh_token"]}).status_code == 401


def test_refresh_token_is_not_an_access_token(client):
    t = client.post(f"{API}/auth/register", json={"email": "luis@x.com", "password": PW}).json()
    assert client.get(f"{API}/me", headers={"Authorization": f"Bearer {t['refresh_token']}"}).status_code == 401


def test_change_password_invalidates_old_sessions(client):
    t = client.post(f"{API}/auth/register", json={"email": "luis@x.com", "password": PW}).json()
    h = {"Authorization": f"Bearer {t['access_token']}"}
    assert client.post(f"{API}/me/password", json={"current_password": "mala-clave-123456", "new_password": "nueva-clave-larga-1"}, headers=h).status_code == 403
    assert client.post(f"{API}/me/password", json={"current_password": PW, "new_password": "corta"}, headers=h).status_code == 422
    r = client.post(f"{API}/me/password", json={"current_password": PW, "new_password": "nueva-clave-larga-1"}, headers=h)
    assert r.status_code == 200
    assert client.get(f"{API}/me", headers=h).status_code == 401                       # access viejo muerto
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": t["refresh_token"]}).status_code == 401
    assert client.get(f"{API}/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).status_code == 200
    assert client.post(f"{API}/auth/login", json={"email": "luis@x.com", "password": PW}).status_code == 401
    _login(client, pw="nueva-clave-larga-1")


def test_change_email(client, auth):
    register(client, "otro@x.com")
    assert client.put(f"{API}/me/email", json={"email": "otro@x.com", "password": PW}, headers=auth).status_code == 409
    assert client.put(f"{API}/me/email", json={"email": "x", "password": PW}, headers=auth).status_code == 422
    assert client.put(f"{API}/me/email", json={"email": "Nuevo@X.com", "password": "mal-mal-mal-mal"}, headers=auth).status_code == 403
    assert client.put(f"{API}/me/email", json={"email": "Nuevo@X.com", "password": PW}, headers=auth).json()["email"] == "nuevo@x.com"
    _login(client, "nuevo@x.com")


@pytest.fixture
def populated(client, auth, monkeypatch):
    monkeypatch.setattr("app.services.runner.build_sources", lambda c=None: [StubSource("ok", JOBS)])
    assert client.post(f"{API}/me/cv", files=CV, headers=auth).status_code == 200
    client.put(f"{API}/me/preferences", json={"countries": ["CO"], "target_roles": ["analista soc"], "languages": ["es"]}, headers=auth)
    client.post(f"{API}/me/searches", json={}, headers=auth)
    jid = client.get(f"{API}/me/jobs", headers=auth).json()["items"][0]["id"]
    assert client.post(f"{API}/me/jobs/{jid}/analyze", headers=auth).status_code == 200
    assert client.post(f"{API}/me/jobs/{jid}/tailor", json={}, headers=auth).status_code == 200
    return auth


def _counts():
    from sqlmodel import SQLModel
    with Session(engine) as s:
        return {t.name: s.execute(text(f'SELECT COUNT(*) FROM "{t.name}"')).scalar()  # noqa: S608
                for t in SQLModel.metadata.sorted_tables}


def test_export_contains_all_user_data_and_no_secrets(client, populated):
    r = client.get(f"{API}/me/export", headers=populated)
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    d = r.json()
    assert d["user"]["email"] == "luis@x.com"
    assert "password_hash" not in r.text and "token_hash" not in r.text and "$argon2" not in r.text
    data = d["data"]
    assert len(data["jobrow"]) == 2 and len(data["tailoredrow"]) == 1 and len(data["searchrun"]) == 1
    assert data["profilerow"][0]["data"]["full_name"] and {e["kind"] for e in data["jobevent"]} >= {"discovered", "analyzed", "tailored"}
    assert "refreshtoken" not in data


def test_export_does_not_leak_other_users(client, populated):
    other = register(client, "otra@x.com")
    d = client.get(f"{API}/me/export", headers=other).json()
    assert d["user"]["email"] == "otra@x.com" and all(rows == [] for rows in d["data"].values())


def test_delete_account_removes_everything_and_only_theirs(client, populated, monkeypatch):
    other = register(client, "otra@x.com")
    client.post(f"{API}/me/cv", files=CV, headers=other)
    before = _counts()
    assert before["jobrow"] == 2 and before["tailoredrow"] == 1
    me = client.get(f"{API}/me", headers=populated).json()["id"]
    assert client.delete(f"{API}/me", headers=populated).status_code == 204
    after = _counts()
    assert after["tailoredrow"] == 0 and after["jobrow"] == 0 and after["jobevent"] == 0 and after["searchrun"] == 0
    assert after["user"] == 1 and after["profilerow"] == 1          # solo queda la otra usuaria
    with Session(engine) as s:
        assert s.execute(text("SELECT COUNT(*) FROM refreshtoken WHERE user_id=:u"), {"u": me}).scalar() == 0
    assert client.get(f"{API}/me", headers=populated).status_code == 401
    assert client.post(f"{API}/auth/login", json={"email": "luis@x.com", "password": PW}).status_code == 401
    assert client.get(f"{API}/me/profile", headers=other).status_code == 200


def test_delete_covers_future_tables_generically(client, auth):
    """Una tabla nueva (de otro módulo) con user_id o colgando de una tabla del usuario se borra sola."""
    from sqlmodel import Field, SQLModel

    class _Extra(SQLModel, table=True):
        __tablename__ = "zz_extra_test"
        id: int | None = Field(default=None, primary_key=True)
        job_id: int = Field(foreign_key="jobrow.id")

    class _Mine(SQLModel, table=True):
        __tablename__ = "zz_mine_test"
        id: int | None = Field(default=None, primary_key=True)
        user_id: int = Field(foreign_key="user.id")

    try:
        SQLModel.metadata.create_all(engine)
        uid = client.get(f"{API}/me", headers=auth).json()["id"]
        with Session(engine) as s:
            s.execute(text("INSERT INTO jobrow (user_id, dedup_key, data, status, first_seen, last_seen) VALUES (:u,'k','{}','new',datetime('now'),datetime('now'))"), {"u": uid})
            jid = s.execute(text("SELECT id FROM jobrow")).scalar()
            s.execute(text("INSERT INTO zz_extra_test (job_id) VALUES (:j)"), {"j": jid})
            s.execute(text("INSERT INTO zz_mine_test (user_id) VALUES (:u)"), {"u": uid})
            s.commit()
        exp = client.get(f"{API}/me/export", headers=auth).json()["data"]
        assert len(exp["zz_extra_test"]) == 1 and len(exp["zz_mine_test"]) == 1
        assert client.delete(f"{API}/me", headers=auth).status_code == 204
        c = _counts()
        assert c["zz_extra_test"] == 0 and c["zz_mine_test"] == 0 and c["user"] == 0
    finally:
        SQLModel.metadata.remove(_Extra.__table__)
        SQLModel.metadata.remove(_Mine.__table__)
        with Session(engine) as s:
            s.execute(text("DROP TABLE IF EXISTS zz_extra_test"))
            s.execute(text("DROP TABLE IF EXISTS zz_mine_test"))
            s.commit()
