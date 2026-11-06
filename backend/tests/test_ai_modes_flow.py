"""Flujo COMPLETO por API en los tres modos de IA: subir CV, buscar (fuentes simuladas), evaluar, analizar, adaptar CV,
PDF, preparar, carta y aplicar. En `local` y `api` el LLM es un servidor simulado (MockTransport) que habla el protocolo real
de cada backend; en `none` no existe ningún LLM. Además: consentimiento, presupuesto, privacidad y estado."""
import pytest
from fastapi.testclient import TestClient

from app.ai import budget
from app.ai.models import LLMUsage  # noqa: F401
from app.config import settings
from app.db import JobRow
from app.main import app
from tests.ai_support import KEY, MockProvider, set_mode
from tests.conftest import register
from tests.shared import API, CV, JOBS, StubSource
from tests.test_cv_audit import PROFILE

MODES = [("none", "openai"), ("local", "openai"), ("api", "openai"), ("api", "anthropic")]


@pytest.fixture
def raw_client():
    """Cliente SIN dependencias sustituidas: usa la fábrica real de LLM del modo configurado."""
    with TestClient(app) as c:
        yield c


def _setup(client, monkeypatch, mode, provider, consent=True):
    mock = set_mode(monkeypatch, mode, provider)
    monkeypatch.setattr("app.services.runner.build_sources", lambda c=None: [StubSource("ok", JOBS), StubSource("roto", [])])
    auth = register(client)
    assert client.post(f"{API}/me/cv", files=CV, headers=auth).status_code == 200
    client.put(f"{API}/me/preferences", json={"countries": ["CO"], "target_roles": ["analista soc"], "languages": ["es"]}, headers=auth)
    assert client.put(f"{API}/me/profile", json=PROFILE.model_dump(mode="json"), headers=auth).status_code == 200
    if mode == "api" and consent:
        assert client.put(f"{API}/me/ai/consent", json={"accepted": True}, headers=auth).status_code == 200
    assert client.post(f"{API}/me/searches", json={}, headers=auth).status_code == 202
    jid = next(j["id"] for j in client.get(f"{API}/me/jobs", headers=auth).json()["items"] if j["job"]["title"] == "Analista SOC")
    return mock, auth, jid


@pytest.mark.parametrize("mode,provider", MODES)
def test_full_flow_works_in_every_mode(raw_client, monkeypatch, mode, provider):
    c = raw_client
    mock, auth, jid = _setup(c, monkeypatch, mode, provider)
    ai_on = mode != "none"

    ev = c.get(f"{API}/me/jobs/{jid}/evaluation", headers=auth)                       # evaluación: determinista en todos los modos
    assert ev.status_code == 200 and 1 <= ev.json()["score"] <= 5

    a = c.post(f"{API}/me/jobs/{jid}/analyze", headers=auth)
    assert a.status_code == 200, a.text
    body = a.json()
    assert body["match"] > 0 and body["gaps"]
    assert body["ai"]["ai_used"] is ai_on
    if not ai_on:
        assert body["ai"]["ai_reason"] == "mode_none" and "sin IA" in body["ai"]["ai_message"]
    st = {g["keyword"]: g["status"] for g in body["gaps"]}
    assert st["AWS"] == "missing"                                                    # lo que no está en el perfil, no se inventa

    t = c.post(f"{API}/me/jobs/{jid}/tailor", json={}, headers=auth)
    assert t.status_code == 200, t.text
    tj = t.json()
    assert tj["ai"]["ai_used"] is ai_on
    cv = tj["cv"]
    assert "AWS" not in cv["text"] and cv["ats"]["score"] >= 60 and cv["lang"] == "es"
    if not ai_on:
        assert cv["trace"][0].startswith("[sin-ia]") and tj["ai"]["ai_reason"] == "mode_none"
    t2 = c.post(f"{API}/me/jobs/{jid}/tailor", json={"confirmed": ["AWS"]}, headers=auth).json()
    assert "AWS" in t2["cv"]["text"]                                                 # confirmada por el usuario: ahora sí

    pdf = c.get(f"{API}/me/cvs/{t2['id']}.pdf", headers=auth)
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert c.get(f"{API}/me/cvs/{t2['id']}.docx", headers=auth).status_code == 200

    letter = c.post(f"{API}/me/jobs/{jid}/cover-letter", json={}, headers=auth)
    assert letter.status_code == 200 and len(letter.json()["text"].split()) > 30
    if not ai_on:
        assert letter.json()["source"] == "template" and letter.json()["ai"]["ai_used"] is False

    pr = c.post(f"{API}/me/jobs/{jid}/prep", json={"use_llm": True}, headers=auth)
    assert pr.status_code == 202
    task = c.get(f"{API}/me/tasks/{pr.json()['id']}", headers=auth).json()
    assert task["status"] == "done", task
    prep = c.get(f"{API}/me/jobs/{jid}/prep", headers=auth).json()
    assert prep["stories"]["stories"] and prep["questions"]["questions"]
    assert prep["stories"]["ai_used"] is False                                       # determinista por diseño
    if not ai_on:
        assert prep["questions"]["ai_used"] is False and prep["questions"]["ai_reason"] == "mode_none"

    ap = c.post(f"{API}/me/jobs/{jid}/apply", headers=auth).json()                    # el sistema nunca envía nada
    assert ap == {"url": "https://ats.b/apply/1", "external": True}

    if mode == "none":
        assert mock.requests == []                                                    # ninguna petición a ningún LLM
    else:
        assert mock.requests, "se esperaba tráfico hacia el LLM simulado"


def test_cv_upload_without_ai_reports_what_is_missing(raw_client, monkeypatch):
    set_mode(monkeypatch, "none")
    auth = register(raw_client)
    txt = "Texto de CV sin estructura reconocible que menciona gestión y atención al público durante varios años " * 3
    r = raw_client.post(f"{API}/me/cv/preview", files={"file": ("cv.txt", txt.encode())}, headers=auth)
    assert r.status_code == 200
    j = r.json()
    assert j["ai"]["ai_used"] is False and j["ai"]["ai_reason"] == "mode_none"
    assert j["report"]["llm_used"] is False and j["report"]["needs_review"]            # el panel sabe qué revisar
    assert any("sin IA" in w for w in j["report"]["warnings"])
    up = raw_client.post(f"{API}/me/cv", files={"file": ("cv.txt", txt.encode())}, headers=auth)
    assert up.status_code == 200 and up.headers["X-AI-Used"] == "false" and up.headers["X-AI-Reason"] == "mode_none"


def test_api_mode_without_consent_falls_back_to_no_ai(raw_client, monkeypatch):
    mock, auth, jid = _setup(raw_client, monkeypatch, "api", "openai", consent=False)
    me = raw_client.get(f"{API}/me/ai", headers=auth).json()
    assert me["you"]["ai_enabled"] is False and me["you"]["reason"] == "no_consent" and me["you"]["consent"] is False
    a = raw_client.post(f"{API}/me/jobs/{jid}/analyze", headers=auth)
    assert a.status_code == 200 and a.json()["ai"] == {**a.json()["ai"], "ai_used": False, "ai_reason": "no_consent"}
    t = raw_client.post(f"{API}/me/jobs/{jid}/tailor", json={}, headers=auth)
    assert t.status_code == 200 and t.json()["ai"]["ai_reason"] == "no_consent" and t.json()["cv"]["trace"][0].startswith("[sin-ia]")
    assert mock.requests == []                                                         # sin consentimiento NADA sale
    raw_client.put(f"{API}/me/ai/consent", json={"accepted": True}, headers=auth)
    assert raw_client.post(f"{API}/me/jobs/{jid}/analyze?force=true", headers=auth).json()["ai"]["ai_used"] is True
    raw_client.put(f"{API}/me/ai/consent", json={"accepted": False}, headers=auth)     # revocar vuelve a cortar la salida
    n = len(mock.requests)
    assert raw_client.post(f"{API}/me/jobs/{jid}/analyze?force=true", headers=auth).json()["ai"]["ai_reason"] == "no_consent"
    assert len(mock.requests) == n


def test_budget_exhaustion_degrades_with_clear_notice_not_500(raw_client, monkeypatch):
    mock, auth, jid = _setup(raw_client, monkeypatch, "api", "openai")
    monkeypatch.setattr(settings, "ai_budget_user_tokens_per_day", 10_000)
    budget.record(1, "gpt-x", "openai", 9_000, 2_000)                                  # el usuario ya gastó su presupuesto
    n = len(mock.requests)
    a = raw_client.post(f"{API}/me/jobs/{jid}/analyze", headers=auth)
    assert a.status_code == 200 and a.json()["ai"]["ai_used"] is False and a.json()["ai"]["ai_reason"] == "budget_exhausted"
    assert "presupuesto" in a.json()["ai"]["ai_message"]
    t = raw_client.post(f"{API}/me/jobs/{jid}/tailor", json={}, headers=auth)
    assert t.status_code == 200 and t.json()["ai"]["ai_reason"] == "budget_exhausted" and "AWS" not in t.json()["cv"]["text"]
    assert len(mock.requests) == n
    me = raw_client.get(f"{API}/me/ai", headers=auth).json()
    assert me["you"]["budget"]["exhausted"] is True and me["you"]["reason"] == "budget_exhausted"


def test_budget_runs_out_midway_and_the_operation_still_completes(raw_client, monkeypatch):
    mock, auth, jid = _setup(raw_client, monkeypatch, "api", "openai")
    monkeypatch.setattr(settings, "ai_budget_user_calls_per_day", 1)                   # solo cabe UNA llamada
    t = raw_client.post(f"{API}/me/jobs/{jid}/tailor", json={}, headers=auth)
    assert t.status_code == 200, t.text
    assert t.json()["ai"]["ai_reason"] == "budget_exhausted" and t.json()["cv"]["ats"]["score"] >= 60


def test_llm_outage_degrades_instead_of_502(raw_client, monkeypatch):
    import httpx
    mock, auth, jid = _setup(raw_client, monkeypatch, "api", "openai")
    mock.script = [httpx.Response(500, json={})] * 60                                  # el proveedor está caído
    monkeypatch.setattr(settings, "llm_api_retries", 1)
    a = raw_client.post(f"{API}/me/jobs/{jid}/analyze", headers=auth)
    assert a.status_code == 200 and a.json()["ai"]["ai_used"] is False and a.json()["ai"]["ai_reason"] == "llm_unavailable"
    t = raw_client.post(f"{API}/me/jobs/{jid}/tailor", json={}, headers=auth)
    assert t.status_code == 200 and t.json()["ai"]["ai_reason"] == "llm_unavailable"


def test_no_contact_data_in_any_prompt_sent_in_api_mode(raw_client, monkeypatch):
    mock, auth, jid = _setup(raw_client, monkeypatch, "api", "openai")
    p = PROFILE.model_copy(deep=True)
    p.email, p.phone, p.location = "luis.secreto@correo-privado.com", "+57 310 555 4433", "Calle 45 # 12-30, Bogotá"
    p.experience[0].bullets.append("Contacto del equipo: soporte@acme-interno.com o +57 601 777 8899 (ver https://wiki.acme.com/guardia)")
    assert raw_client.put(f"{API}/me/profile", json=p.model_dump(mode="json"), headers=auth).status_code == 200
    for step in (lambda: raw_client.post(f"{API}/me/jobs/{jid}/analyze?force=true", headers=auth),
                 lambda: raw_client.post(f"{API}/me/jobs/{jid}/tailor", json={"confirmed": ["AWS"]}, headers=auth),
                 lambda: raw_client.post(f"{API}/me/jobs/{jid}/cover-letter", json={}, headers=auth),
                 lambda: raw_client.post(f"{API}/me/jobs/{jid}/prep", json={"use_llm": True}, headers=auth)):
        assert step().status_code in (200, 202)
    sent = mock.prompts
    assert mock.requests and "Splunk" in sent                                           # sí viaja lo que hace falta...
    for secret in ("luis.secreto", "correo-privado", "310 555 4433", "555 4433", "Calle 45", "soporte@acme", "601 777 8899", "wiki.acme.com",
                   "Luis Gómez", "Bogotá"):
        assert secret not in sent, f"se filtró '{secret}' al proveedor"
    # la clave solo viaja en la cabecera de autenticación, nunca en el cuerpo
    assert all(KEY not in str(r["body"]) for r in mock.requests)
    assert all(r["headers"].get("authorization") == f"Bearer {KEY}" for r in mock.requests)


def test_cv_text_contact_lines_are_redacted_when_the_builder_asks_the_model(raw_client, monkeypatch):
    mock = set_mode(monkeypatch, "api", "anthropic")
    auth = register(raw_client)
    raw_client.put(f"{API}/me/ai/consent", json={"accepted": True}, headers=auth)
    txt = ("jlopez@privado.org | +34 611 222 333 | Avenida de los Pinos 45, 3B Madrid\nlinkedin.com/in/jlopez-secreto\n"
           "Trabajo con equipos distintos en proyectos variados de tecnología desde hace años y busco nuevos retos profesionales.\n" * 2)
    r = raw_client.post(f"{API}/me/cv/preview", files={"file": ("cv.txt", txt.encode())}, headers=auth)
    assert r.status_code == 200
    assert mock.requests, "el builder debería haber consultado al modelo (nombre/cargo ambiguos)"
    for secret in ("jlopez@privado", "611 222 333", "Avenida de los Pinos", "jlopez-secreto"):
        assert secret not in mock.prompts
    assert all(r["headers"].get("x-api-key") == KEY for r in mock.requests)


def test_status_endpoint_by_mode_and_never_exposes_the_key(raw_client, monkeypatch):
    for mode, sends in (("none", False), ("local", False), ("api", True)):
        set_mode(monkeypatch, mode, "anthropic")
        s = raw_client.get(f"{API}/ai/status")                                          # sin autenticación
        assert s.status_code == 200
        j = s.json()
        assert j["mode"] == mode and j["sends_data_to_third_party"] is sends
        assert set(j["capabilities"]) == {"cv_reading", "requirements", "gaps", "cv_writing", "cover_letter", "prep", "evaluation"}
        assert KEY not in s.text and "SECRET" not in s.text
        if mode == "none":
            assert j["provider"] is None and not any(c["ai_active"] for c in j["capabilities"].values())
            assert j["capabilities"]["cv_writing"]["method"] == "extractive" and j["capabilities"]["requirements"]["method"].startswith("lexicon")
        if mode == "local":
            assert j["capabilities"]["cv_writing"]["ai_active"] is True and not any(c["sends_data"] for c in j["capabilities"].values())
        if mode == "api":
            assert j["provider"]["provider"] == "anthropic" and j["provider"]["host"] == "anthropic.test" and j["provider"]["key_configured"] is True
            assert j["privacy"]["never_sent"] and j["consent_required"] is True and j["budget_exhausted"] is False
            ready = raw_client.get("/ready")
            assert KEY not in ready.text and ready.json()["checks"]["llm"]["key_configured"] is True


def test_status_with_session_shows_personal_consent_and_budget(raw_client, monkeypatch):
    set_mode(monkeypatch, "api")
    auth = register(raw_client)
    j = raw_client.get(f"{API}/ai/status", headers=auth).json()
    assert j["you"]["ai_enabled"] is False and j["you"]["reason"] == "no_consent"
    assert not any(c["ai_active"] for c in j["capabilities"].values())                  # sin consentimiento, nada usa IA para ti
    raw_client.put(f"{API}/me/ai/consent", json={"accepted": True}, headers=auth)
    j = raw_client.get(f"{API}/ai/status", headers=auth).json()
    assert j["you"]["ai_enabled"] is True and j["capabilities"]["cv_writing"]["sends_data"] is True
    assert j["you"]["budget"]["limits"]["user_tokens_per_day"] == settings.ai_budget_user_tokens_per_day


def test_api_mode_without_key_is_safe_and_visible(raw_client, monkeypatch):
    from pydantic import SecretStr
    mock, auth, jid = _setup(raw_client, monkeypatch, "api", "openai")
    monkeypatch.setattr(settings, "llm_api_key", SecretStr(""))
    a = raw_client.post(f"{API}/me/jobs/{jid}/analyze?force=true", headers=auth).json()
    assert a["ai"]["ai_reason"] == "not_configured" and "falta la clave" in a["ai"]["ai_message"]
    assert "warning" in raw_client.get("/ready").json()["checks"]["llm"]


def test_global_budget_exhausted_is_public(raw_client, monkeypatch):
    mock, auth, jid = _setup(raw_client, monkeypatch, "api", "openai")
    monkeypatch.setattr(settings, "ai_budget_global_calls_per_day", 1)
    budget.record(None, "m", "openai", 1, 1)
    assert raw_client.get(f"{API}/ai/status").json()["budget_exhausted"] is True
    assert raw_client.post(f"{API}/me/jobs/{jid}/analyze?force=true", headers=auth).json()["ai"]["ai_reason"] == "budget_exhausted"
    assert JobRow and MockProvider
