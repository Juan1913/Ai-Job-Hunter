import json
import threading
import time

import httpx
import pytest

from app.config import settings
from app.llm.client import FairGate, LLMBusy, LLMClient, LLMInvalidJSON, LLMTimeout, LLMUnavailable, list_models, model_present
from app.llm.jsonrepair import repair_json
from app.observability import metrics


def ollama_reply(content, pt=11, ct=7):
    return {"message": {"role": "assistant", "content": content}, "prompt_eval_count": pt, "eval_count": ct}


def make(handler, **kw):
    return LLMClient(base_url="http://llm:11434/v1", model="m", transport=httpx.MockTransport(handler), sleep=lambda s: None, **kw)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(settings, "llm_backoff_s", 0.0)
    metrics.reset()


def test_complete_ollama_payload_and_tokens():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["body"] = str(req.url), json.loads(req.content)
        return httpx.Response(200, json=ollama_reply("hola"))

    assert make(handler).complete("sys", "usr") == "hola"
    assert seen["url"] == "http://llm:11434/api/chat"
    b = seen["body"]
    assert b["stream"] is False and b["keep_alive"] == settings.llm_keep_alive
    assert b["options"]["num_ctx"] == settings.llm_num_ctx and b["options"]["num_predict"] == settings.llm_num_predict
    assert "format" not in b
    assert metrics.counter("jh_llm_tokens_total", model="m", kind="completion") == 7
    assert metrics.counter("jh_llm_calls_total", model="m", result="ok") == 1


def test_json_mode_native_and_openai_backend():
    bodies = []

    def handler(req):
        bodies.append((str(req.url), json.loads(req.content)))
        if "/api/chat" in str(req.url):
            return httpx.Response(200, json=ollama_reply('{"a": 1}'))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"a": 2}'}}], "usage": {"prompt_tokens": 3, "completion_tokens": 4}})

    assert make(handler).complete_json("s", "u") == {"a": 1}
    assert bodies[0][1]["format"] == "json"
    assert make(handler, backend="openai").complete_json("s", "u") == {"a": 2}
    assert bodies[1][0].endswith("/v1/chat/completions") and bodies[1][1]["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize("raw,expected", [
    ('{"a": 1,}', {"a": 1}),
    ('Claro, aquí tienes:\n```json\n{"a": [1, 2,]}\n```\nEspero que sirva', {"a": [1, 2]}),
    ("{'a': 'b', 'c': True, d: None}", {"a": "b", "c": True, "d": None}),
    ("{“a”: “hola”}", {"a": "hola"}),
    ('{"a": "linea1\nlinea2"}', {"a": "linea1\nlinea2"}),
    ('{"a": {"b": [1, 2', {"a": {"b": [1, 2]}}),                    # truncado
    ('{"a": "x", "b": "y"} texto { sobrante', {"a": "x", "b": "y"}),
    ('{"a": "it\'s ok"}', {"a": "it's ok"}),
    ('[{"k": 1}]', [{"k": 1}]),
])
def test_repair_json(raw, expected):
    assert repair_json(raw) == expected


def test_repair_json_irreparable():
    for raw in ("", "no hay nada", "{{{{ ]]]"):
        with pytest.raises(ValueError):
            repair_json(raw)


def test_complete_json_retries_then_succeeds():
    replies = iter(["esto no es json", '{"ok": true}'])
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json=ollama_reply(next(replies)))

    assert make(handler).complete_json("s", "u") == {"ok": True}
    assert len(calls) == 2
    assert metrics.counter("jh_llm_retries_total", reason="invalid_json") == 1


def test_complete_json_gives_up_with_typed_error(monkeypatch):
    monkeypatch.setattr(settings, "llm_json_retries", 1)
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json=ollama_reply("basura"))

    with pytest.raises(LLMInvalidJSON) as ei:
        make(handler).complete_json("s", "u")
    assert ei.value.kind == "invalid_json" and len(calls) == 2


def test_network_error_retries_with_backoff_then_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "llm_retries", 2)
    sleeps, n = [], []

    def handler(req):
        n.append(1)
        raise httpx.ConnectError("sin servidor")

    c = LLMClient(base_url="http://llm:11434/v1", model="m", transport=httpx.MockTransport(handler), sleep=sleeps.append)
    monkeypatch.setattr(settings, "llm_backoff_s", 0.5)
    with pytest.raises(LLMUnavailable) as ei:
        c.complete("s", "u")
    assert ei.value.kind == "unavailable" and len(n) == 3 and sleeps == [0.5, 1.0]


def test_5xx_retried_and_recovers():
    seq = iter([503, 200])

    def handler(req):
        return httpx.Response(next(seq), json=ollama_reply("listo"))

    assert make(handler).complete("s", "u") == "listo"


def test_4xx_is_not_retried():
    n = []

    def handler(req):
        n.append(1)
        return httpx.Response(404, json={"error": "model not found"})

    with pytest.raises(LLMUnavailable):
        make(handler).complete("s", "u")
    assert len(n) == 1


def test_timeout_is_typed(monkeypatch):
    monkeypatch.setattr(settings, "llm_retries", 0)

    def handler(req):
        raise httpx.ReadTimeout("lento")

    with pytest.raises(LLMTimeout) as ei:
        make(handler).complete("s", "u")
    assert ei.value.kind == "timeout"


def test_timeouts_are_separate():
    from app.llm.client import _timeouts
    t = _timeouts()
    assert t.connect == settings.llm_connect_timeout_s and t.read == settings.llm_timeout_s and t.connect != t.read


def test_gate_fifo_and_per_user_round_robin():
    gate = FairGate(1)
    gate.acquire("x")                       # ocupa el único hueco
    order, threads = [], []

    def waiter(user, tag):
        gate.acquire(user, 5)
        order.append(tag)
        gate.release()

    # a1,a2,a3 llegan antes que b1: round-robin => a1, b1, a2, a3
    for user, tag in (("a", "a1"), ("a", "a2"), ("a", "a3"), ("b", "b1")):
        t = threading.Thread(target=waiter, args=(user, tag))
        t.start()
        threads.append(t)
        while gate.queued < len(threads):
            time.sleep(0.005)
    assert gate.queued == 4
    gate.release()
    for t in threads:
        t.join(5)
    assert order == ["a1", "b1", "a2", "a3"]
    assert gate.active == 0


def test_gate_timeout_raises_busy_and_cleans_queue():
    gate = FairGate(1)
    gate.acquire("x")
    with pytest.raises(LLMBusy):
        gate.acquire("y", 0.05)
    assert gate.queued == 0
    gate.release()
    gate.acquire("z", 0.05)


def test_global_concurrency_is_serialized(monkeypatch):
    monkeypatch.setattr(settings, "llm_max_concurrency", 1)
    running, peak, lock = 0, 0, threading.Lock()

    def handler(req):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.03)
        with lock:
            running -= 1
        return httpx.Response(200, json=ollama_reply("ok"))

    c = make(handler)
    ts = [threading.Thread(target=c.complete, args=("s", "u")) for _ in range(5)]
    [t.start() for t in ts]
    [t.join(10) for t in ts]
    assert peak == 1
    assert metrics.hist_count("jh_llm_latency_seconds", model="m") == 5


def test_list_models_and_presence():
    def handler(req):
        assert str(req.url) == "http://llm:11434/api/tags"
        return httpx.Response(200, json={"models": [{"name": "qwen2.5:3b-instruct"}, {"name": "nomic-embed-text:latest"}]})

    names = list_models("http://llm:11434/v1", transport=httpx.MockTransport(handler))
    assert model_present("qwen2.5:3b-instruct", names) and model_present("nomic-embed-text", names)
    assert not model_present("qwen2.5:7b-instruct", names)


def test_warmup_never_raises():
    def handler(req):
        raise httpx.ConnectError("x")

    assert make(handler).warmup() is False
