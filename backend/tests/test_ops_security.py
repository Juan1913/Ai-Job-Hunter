import time

import pytest

from app.config import settings
from app.middleware import RateLimiter, rate_limiter
from app.security import LoginGuard, create_token, decode_token
from tests.conftest import register

API = "/api/v1"
PW = "una-clave-larga-123"


@pytest.fixture(autouse=True)
def _clean():
    rate_limiter.reset()
    yield
    rate_limiter.reset()


def test_token_bucket_refills():
    t = [0.0]
    rl = RateLimiter(clock=lambda: t[0])
    assert [rl.allow("k", 60, 2)[0] for _ in range(3)] == [True, True, False]
    ok, wait = rl.allow("k", 60, 2)
    assert not ok and wait >= 1
    t[0] += 1.0                                   # 60/min = 1 token por segundo
    assert rl.allow("k", 60, 2)[0] is True
    assert rl.allow("otra", 60, 2)[0] is True     # claves independientes


def test_rate_limit_per_ip(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_ip_burst", 3)
    monkeypatch.setattr(settings, "rate_ip_per_min", 1)
    rate_limiter.reset()
    codes = [client.get(f"{API}/me/profile").status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]
    r = client.get(f"{API}/me/profile")
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert client.get("/health").status_code == 200       # las sondas no se limitan


def test_rate_limit_per_user_independent(client, monkeypatch):
    a = register(client, "a@x.com")
    b = register(client, "b@x.com")
    monkeypatch.setattr(settings, "rate_user_burst", 2)
    monkeypatch.setattr(settings, "rate_user_per_min", 1)
    rate_limiter.reset()
    assert [client.get(f"{API}/me/stats", headers=a).status_code for _ in range(3)] == [200, 200, 429]
    assert client.get(f"{API}/me/stats", headers=b).status_code == 200


def test_auth_endpoints_stricter(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_auth_burst", 3)
    monkeypatch.setattr(settings, "rate_auth_per_min", 1)
    rate_limiter.reset()
    codes = [client.post(f"{API}/auth/login", json={"email": "no@x.com", "password": "x" * 12}).status_code for _ in range(5)]
    assert codes == [401, 401, 401, 429, 429]


def test_login_lockout_after_failures_and_reset(client, monkeypatch):
    monkeypatch.setattr(settings, "login_max_failures", 3)
    register(client, "luis@x.com")
    bad = {"email": "luis@x.com", "password": "incorrecta-123456"}
    assert [client.post(f"{API}/auth/login", json=bad).status_code for _ in range(3)] == [401, 401, 401]
    r = client.post(f"{API}/auth/login", json={"email": "luis@x.com", "password": PW})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0       # ni con la clave buena
    # un email inexistente se bloquea igual: no revela si existe
    ghost = {"email": "fantasma@x.com", "password": "incorrecta-123456"}
    assert [client.post(f"{API}/auth/login", json=ghost).status_code for _ in range(4)] == [401, 401, 401, 429]


def test_login_guard_expires():
    t = [0.0]
    g = LoginGuard(clock=lambda: t[0])
    for _ in range(settings.login_max_failures):
        g.failure("1.1.1.1", "a@x.com")
    assert g.retry_after("1.1.1.1", "a@x.com") > 0
    t[0] += settings.login_lock_s + 1
    assert g.retry_after("1.1.1.1", "a@x.com") == 0
    g.failure("1.1.1.1", "a@x.com")
    g.success("1.1.1.1", "a@x.com")
    assert g._fails == {}


def test_login_and_register_do_not_differentiate_users(client):
    register(client, "luis@x.com")
    unknown = client.post(f"{API}/auth/login", json={"email": "nadie@x.com", "password": PW})
    wrong = client.post(f"{API}/auth/login", json={"email": "luis@x.com", "password": "otra-clave-larga-1"})
    assert unknown.status_code == wrong.status_code == 401 and unknown.json() == wrong.json()
    dup = client.post(f"{API}/auth/register", json={"email": "luis@x.com", "password": PW})
    assert dup.status_code == 409 and "luis" not in dup.text
    long_pw = client.post(f"{API}/auth/register", json={"email": "z@x.com", "password": "a" * 5000})
    assert long_pw.status_code == 422


def test_security_headers_and_request_id(client):
    r = client.get("/health", headers={"X-Request-ID": "mi-request-id-123"})
    assert r.headers["x-request-id"] == "mi-request-id-123"
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    r2 = client.get("/health", headers={"X-Request-ID": "mal id<script>"})
    assert r2.headers["x-request-id"] != "mal id<script>" and len(r2.headers["x-request-id"]) == 32
    assert client.get("/health").headers["x-request-id"] != r2.headers["x-request-id"]


def test_body_limit_by_header_and_by_stream(client, auth, monkeypatch):
    monkeypatch.setattr(settings, "max_body_bytes", 1000)
    r = client.post(f"{API}/me/cv", files={"file": ("cv.txt", b"x" * 5000)}, headers=auth)
    assert r.status_code == 413


def test_body_limit_when_streaming_without_content_length(monkeypatch):
    """Sin Content-Length fiable (chunked) se corta al contar los bytes recibidos."""
    from fastapi.testclient import TestClient
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    from app.middleware import AppMiddleware

    async def eco(request: Request):
        return PlainTextResponse(str(len(await request.body())))

    monkeypatch.setattr(settings, "max_body_bytes", 1000)
    app = Starlette(routes=[Route("/x", eco, methods=["POST"])])
    app.add_middleware(AppMiddleware)
    c = TestClient(app)

    def gen(n):
        for _ in range(n):
            yield b"y" * 500
    assert c.post("/x", content=gen(1)).text == "500"
    assert c.post("/x", content=gen(10)).status_code == 413


def test_cors_configurable():
    import importlib

    from fastapi.testclient import TestClient

    import app.main as main
    old = settings.cors_origins
    settings.cors_origins = "https://app.example.com"
    try:
        m = importlib.reload(main)
        with TestClient(m.app) as c:
            ok = c.options("/api/v1/auth/login", headers={"Origin": "https://app.example.com", "Access-Control-Request-Method": "POST"})
            bad = c.options("/api/v1/auth/login", headers={"Origin": "https://evil.com", "Access-Control-Request-Method": "POST"})
            assert ok.headers["access-control-allow-origin"] == "https://app.example.com"
            assert "access-control-allow-origin" not in bad.headers
    finally:
        settings.cors_origins = old
        importlib.reload(main)


def test_prod_rejects_wildcard_cors_and_weak_secret():
    from app.config import Settings, validate_production
    with pytest.raises(RuntimeError):
        validate_production(Settings(env="prod", jwt_secret="corto"))
    with pytest.raises(RuntimeError):
        validate_production(Settings(env="prod", jwt_secret="x" * 40, cors_origins="*"))
    validate_production(Settings(env="prod", jwt_secret="x" * 40, cors_origins="https://a.com"))


def test_daily_quotas(client, auth, monkeypatch):
    from tests.shared import CV
    monkeypatch.setattr("app.services.runner.build_sources", lambda c=None: [])
    assert client.post(f"{API}/me/cv", files=CV, headers=auth).status_code == 200
    client.put(f"{API}/me/preferences", json={"countries": ["CO"], "target_roles": ["analista soc"], "languages": ["es"]}, headers=auth)
    monkeypatch.setattr(settings, "quota_searches_per_day", 1)
    assert client.post(f"{API}/me/searches", json={}, headers=auth).status_code == 202
    r = client.post(f"{API}/me/searches", json={}, headers=auth)
    assert r.status_code == 429 and "búsquedas" in r.json()["detail"]
    other = register(client, "otro@x.com")
    client.post(f"{API}/me/cv", files=CV, headers=other)
    assert client.post(f"{API}/me/searches", json={}, headers=other).status_code != 429     # cuota por usuario


def test_quota_zero_means_unlimited(client, auth, monkeypatch):
    from tests.shared import CV
    monkeypatch.setattr("app.services.runner.build_sources", lambda c=None: [])
    client.post(f"{API}/me/cv", files=CV, headers=auth)
    monkeypatch.setattr(settings, "quota_searches_per_day", 0)
    for _ in range(3):
        assert client.post(f"{API}/me/searches", json={}, headers=auth).status_code == 202


def test_jwt_typ_and_expiry(monkeypatch):
    import jwt
    tok = create_token(7)
    assert decode_token(tok) == 7
    forged = jwt.encode({"sub": "7", "typ": "refresh", "exp": int(time.time()) + 60}, settings.jwt_secret, algorithm="HS256")
    assert decode_token(forged) is None
    expired = jwt.encode({"sub": "7", "typ": "access", "exp": int(time.time()) - 5}, settings.jwt_secret, algorithm="HS256")
    assert decode_token(expired) is None
    notyp = jwt.encode({"sub": "7", "exp": int(time.time()) + 60}, settings.jwt_secret, algorithm="HS256")
    assert decode_token(notyp) is None
