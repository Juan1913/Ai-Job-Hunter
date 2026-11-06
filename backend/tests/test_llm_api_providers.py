"""Proveedores externos del cliente LLM (OpenAI-compatible y Anthropic Messages) con MockTransport: protocolo, claves,
429/Retry-After, redacción de contacto, reparación de JSON y contabilidad de tokens. Sin red, sin SDKs."""
import json

import httpx
import pytest

from app.ai import budget
from app.ai.privacy import Redactor, profile_for_prompt, redact_secrets, scrub
from app.config import settings
from app.llm.client import LLMClient, LLMDisabled, LLMInvalidJSON, LLMUnavailable, get_api_gate
from app.observability import metrics
from tests.ai_support import KEY


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(settings, "llm_backoff_s", 0.0)
    metrics.reset()


def client(handler, backend="openai", sleeps=None, **kw):
    base = "https://llm.test/v1" if backend == "openai" else "https://anthropic.test"
    return LLMClient(base_url=base, model="m-api", backend=backend, transport=httpx.MockTransport(handler), api_key=KEY, external=True,
                     sleep=(sleeps.append if sleeps is not None else (lambda s: None)), retries=3, **kw)


def openai_reply(text, pt=5, ct=3):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": pt, "completion_tokens": ct}})


def test_openai_compatible_request_shape_key_header_and_usage():
    seen, usage = {}, []

    def handler(req):
        seen.update(url=str(req.url), auth=req.headers["authorization"], body=json.loads(req.content))
        return openai_reply('{"a": 1}', 11, 7)

    c = client(handler, on_usage=lambda m, i, o: usage.append((m, i, o)))
    assert c.complete_json("sys", "usr") == {"a": 1}
    assert seen["url"] == "https://llm.test/v1/chat/completions" and seen["auth"] == f"Bearer {KEY}"
    assert seen["body"]["response_format"] == {"type": "json_object"} and seen["body"]["model"] == "m-api"
    assert KEY not in json.dumps(seen["body"])                                   # la clave solo va en la cabecera
    assert usage == [("m-api", 11, 7)]
    assert metrics.counter("jh_llm_tokens_total", model="m-api", kind="prompt") == 11


def test_server_without_native_json_is_retried_without_response_format_and_remembered():
    bodies = []

    def handler(req):
        b = json.loads(req.content)
        bodies.append(b)
        if "response_format" in b:
            return httpx.Response(400, json={"error": {"message": "response_format is not supported by this model"}})
        return openai_reply('{"ok": true}')

    c = client(handler)
    assert c.complete_json("s", "u") == {"ok": True}
    assert "response_format" in bodies[0] and "response_format" not in bodies[1]
    c.complete_json("s", "u")
    assert "response_format" not in bodies[2]                                      # recuerda que ese servidor no lo admite


def test_anthropic_messages_protocol():
    seen, usage = {}, []

    def handler(req):
        seen.update(url=str(req.url), headers=dict(req.headers), body=json.loads(req.content))
        return httpx.Response(200, json={"content": [{"type": "thinking", "thinking": "secreto interno"}, {"type": "text", "text": '{"k": ["v"]}'}],
                                         "stop_reason": "end_turn", "usage": {"input_tokens": 21, "output_tokens": 9}})

    c = client(handler, backend="anthropic", on_usage=lambda m, i, o: usage.append((i, o)))
    assert c.complete_json("Eres un extractor.", "texto") == {"k": ["v"]}
    b = seen["body"]
    assert seen["url"] == "https://anthropic.test/v1/messages"
    assert seen["headers"]["x-api-key"] == KEY and seen["headers"]["anthropic-version"] == settings.llm_api_anthropic_version
    assert "authorization" not in seen["headers"]
    assert "temperature" not in b and "response_format" not in b                   # los modelos 5.x rechazan temperature
    assert b["messages"] == [{"role": "user", "content": "texto"}] and "JSON" in b["system"] and b["system"].startswith("Eres un extractor.")
    assert b["max_tokens"] >= 2048 and b["output_config"] == {"effort": settings.llm_api_anthropic_effort}
    assert usage == [(21, 9)]                                                       # y el bloque 'thinking' no se mezcla en la respuesta


def test_anthropic_refusal_is_permanent_and_not_retried():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json={"content": [], "stop_reason": "refusal", "usage": {}})

    with pytest.raises(LLMUnavailable, match="se negó"):
        client(handler, backend="anthropic").complete("s", "u")
    assert len(calls) == 1


@pytest.mark.parametrize("backend", ["openai", "anthropic"])
def test_429_obeys_retry_after_with_cap_then_succeeds(backend, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_retry_after_max_s", 5.0)
    state = {"n": 0}
    sleeps: list[float] = []

    def handler(req):
        state["n"] += 1
        if state["n"] <= 2:
            return httpx.Response(429, headers={"retry-after": "120" if state["n"] == 1 else "2"}, json={"error": "rate"})
        return openai_reply("hola") if backend == "openai" else httpx.Response(200, json={"content": [{"type": "text", "text": "hola"}], "usage": {}})

    assert client(handler, backend=backend, sleeps=sleeps).complete("s", "u") == "hola"
    assert sleeps == [5.0, 2.0]                                                     # 120 s pedidos -> tope 5 s; luego 2 s
    assert metrics.counter("jh_llm_retries_total", reason="unavailable") == 2


def test_persistent_429_ends_in_typed_error_and_auth_errors_do_not_retry_or_leak_the_key():
    n = []

    def always_429(req):
        n.append(1)
        return httpx.Response(429, json={})

    with pytest.raises(LLMUnavailable, match="429"):
        client(always_429).complete("s", "u")
    assert len(n) == 4                                                              # 1 + 3 reintentos prudentes

    seen = []

    def bad_key(req):
        seen.append(1)
        return httpx.Response(401, text=f"Incorrect API key provided: {KEY}. Check your key (Bearer {KEY})")

    with pytest.raises(LLMUnavailable) as e:
        client(bad_key).complete("s", "u")
    assert len(seen) == 1 and KEY not in str(e.value) and "***" in str(e.value)


def test_network_errors_never_include_the_key():
    def boom(req):
        raise httpx.ConnectError(f"fallo conectando con clave {KEY}")

    with pytest.raises(LLMUnavailable) as e:
        client(boom).complete("s", "u")
    assert KEY not in str(e.value)


def test_json_repair_and_typed_invalid_json_on_external_backends(monkeypatch):
    monkeypatch.setattr(settings, "llm_json_retries", 1)
    c = client(lambda r: openai_reply('Claro:\n```json\n{"a": [1, 2,],}\n```'))
    assert c.complete_json("s", "u") == {"a": [1, 2]}
    bad = client(lambda r: openai_reply("esto no es json, ni lo parece"))
    with pytest.raises(LLMInvalidJSON):
        bad.complete_json("s", "u")


def test_api_queue_allows_parallelism_independent_of_local_cpu_queue(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_max_concurrency", 6)
    monkeypatch.setattr(settings, "llm_max_concurrency", 1)
    assert get_api_gate().slots == 6


def test_outgoing_contact_data_is_redacted_and_restored_in_the_answer():
    seen = {}

    def handler(req):
        seen["body"] = req.content.decode()
        tokens = json.loads(req.content)["messages"][1]["content"]
        return openai_reply(json.dumps({"echo": tokens}))

    user = "Contacto: ana@correo.com, +57 310 456 7788, https://linkedin.com/in/ana-x, Calle 10 # 5-20. Periodo 2015 - 2017."
    out = client(handler).complete_json("Eres un asistente", user)
    sent = seen["body"]
    for s in ("ana@correo.com", "310 456 7788", "linkedin.com/in/ana-x", "Calle 10"):
        assert s not in sent
    assert "2015 - 2017" in sent                                                    # un rango de años no es un teléfono
    assert out["echo"] == user                                                      # si el modelo repite los marcadores, se reinyectan en local


def test_redactor_units():
    r = Redactor()
    t = r.apply("a@b.co y a@b.co otra vez; tel (601) 234-5678; www.mi-sitio.com/cv; Av. Libertador 1234")
    assert t.count("[EMAIL_1]") == 2 and "a@b.co" not in t and "[TELEFONO_1]" in t and "[URL_1]" in t and "[DIRECCION_1]" in t
    assert r.restore(t).startswith("a@b.co y a@b.co")
    assert scrub("1.200 empresas y 45 cuentas; COP 2.800 millones; 18% al 27%") == "1.200 empresas y 45 cuentas; COP 2.800 millones; 18% al 27%"
    assert redact_secrets(f"error con {KEY} y Bearer abcdefghijklmnop1234", KEY) == "error con *** y ***"


def test_profile_for_prompt_drops_contact_and_name():
    from tests.fakes import PROFILE
    d = profile_for_prompt(PROFILE)
    assert not {"email", "phone", "location", "full_name", "preferences"} & set(d) and d["skills"] and d["experience"]


def test_usage_accounting_and_estimated_cost(monkeypatch):
    budget.record(7, "gpt-x", "openai", 1000, 500, budget.cost_usd(2.0, 10.0, 1000, 500))
    budget.record(7, "gpt-x", "openai", 1000, 500, budget.cost_usd(2.0, 10.0, 1000, 500))
    budget.record(None, "gpt-x", "openai", 10, 5)
    st = budget.state(7)
    assert st.user == {"calls": 2, "tokens_in": 2000, "tokens_out": 1000, "tokens": 3000, "cost_usd": 0.014}
    assert st.globally["calls"] == 3 and not st.exhausted
    monkeypatch.setattr(settings, "ai_budget_global_cost_per_day", 0.01)
    assert budget.state(7).exhausted and budget.state(7).scope == "global" and budget.state(7).metric == "cost"
    monkeypatch.setattr(settings, "ai_budget_global_cost_per_day", 0.0)
    monkeypatch.setattr(settings, "ai_budget_user_tokens_per_day", 2500)
    s2 = budget.state(7)
    assert s2.exhausted and s2.scope == "user" and "presupuesto" in s2.message
    assert not budget.state(8).exhausted                                            # otro usuario no se ve afectado por el tope personal


def test_guarded_llm_blocks_calls_when_disabled_and_counts_real_calls(monkeypatch):
    from app.ai.access import AiAccess
    from app.llm.guard import GuardedLLM
    off = GuardedLLM(None, AiAccess(False, "mode_none", "none"), 1)
    with pytest.raises(LLMDisabled, match="IA no disponible") as e:
        off.complete_json("s", "u")
    assert e.value.reason == "mode_none" and not off.enabled
    inner = client(lambda r: openai_reply('{"x": 1}'))
    g = GuardedLLM(inner, AiAccess(True, None, "api", True), 3)
    assert g.complete_json("s", "u") == {"x": 1} and g.calls == 1 and g.model == "m-api"
    monkeypatch.setattr(settings, "ai_budget_user_calls_per_day", 1)
    budget.record(3, "m", "openai", 1, 1)
    with pytest.raises(LLMDisabled) as e2:
        g.complete_json("s", "u")
    assert e2.value.reason == "budget_exhausted" and g.access.reason == "budget_exhausted" and g.calls == 1
