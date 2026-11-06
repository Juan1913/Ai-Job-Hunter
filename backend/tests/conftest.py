import os
import tempfile

_dir = tempfile.mkdtemp()
os.environ["JH_DATABASE_URL"] = f"sqlite:///{_dir}/test.db"
os.environ["JH_RUN_INLINE"] = "true"

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel

from app.api.deps import get_llm, get_writer
from app.db import engine
from app.main import app
from tests.fakes import FakeLLM


@pytest.fixture(autouse=True)
def fresh_db():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    yield


@pytest.fixture
def llm():
    return FakeLLM()


@pytest.fixture
def client(llm):
    app.dependency_overrides[get_llm] = lambda: llm
    app.dependency_overrides[get_writer] = lambda: llm
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def register(client, email="luis@x.com", pw="una-clave-larga-123"):
    r = client.post("/api/v1/auth/register", json={"email": email, "password": pw})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def auth(client):
    return register(client)


@pytest.fixture
def ready(client, auth, monkeypatch):
    """Usuario con CV subido, preferencias y fuentes simuladas (una sana y una rota)."""
    from tests.shared import API, CV, JOBS, StubSource
    monkeypatch.setattr("app.services.runner.build_sources", lambda c=None: [StubSource("ok", JOBS), StubSource("roto", [])])
    assert client.post(f"{API}/me/cv", files=CV, headers=auth).status_code == 200
    client.put(f"{API}/me/preferences", json={"countries": ["CO"], "target_roles": ["analista soc"], "languages": ["es"]}, headers=auth)
    return auth
