import json
import logging

import httpx
import pytest

from app import observability as obs
from app.api import health
from app.config import settings
from app.llm import client as llm_client
from app.middleware import rate_limiter
from app.observability import Metrics, metrics


@pytest.fixture(autouse=True)
def _clean():
    rate_limiter.reset()
    health._cache.clear()
    metrics.reset()


def test_health_is_pure_liveness(client, monkeypatch):
    monkeypatch.setattr(health, "list_models", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debe tocar el LLM")))
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"ok": True}


def _tags(names):
    def fake(*a, **k):
        return names
    return fake


def test_ready_ok_when_models_present(client, monkeypatch):
    monkeypatch.setattr(health, "list_models", _tags(["qwen2.5:3b-instruct", "qwen2.5:7b-instruct", "nomic-embed-text:latest"]))
    r = client.get("/ready")
    assert r.status_code == 200 and r.json()["checks"]["db"]["ok"] and r.json()["checks"]["llm"]["missing"] == []


def test_ready_503_when_model_missing(client, monkeypatch):
    monkeypatch.setattr(health, "list_models", _tags(["qwen2.5:3b-instruct"]))
    r = client.get("/ready")
    assert r.status_code == 503
    assert set(r.json()["checks"]["llm"]["missing"]) == {"qwen2.5:7b-instruct", settings.llm_model_embed}


def test_ready_503_when_llm_down_and_skippable(client, monkeypatch):
    def boom(*a, **k):
        raise llm_client.LLMUnavailable("sin servidor")
    monkeypatch.setattr(health, "list_models", boom)
    r = client.get("/ready")
    assert r.status_code == 503 and "no alcanzable" in r.json()["checks"]["llm"]["error"]
    health._cache.clear()
    monkeypatch.setattr(settings, "ready_requires_llm", False)
    assert client.get("/ready").status_code == 200


def test_ready_503_when_db_down(client, monkeypatch):
    monkeypatch.setattr(settings, "ready_requires_llm", False)
    monkeypatch.setattr(health, "check_db", lambda: {"ok": False, "error": "caída"})
    assert client.get("/ready").status_code == 503


def test_metrics_endpoint_prometheus_format(client, auth, monkeypatch):
    client.get("/health")
    client.get("/api/v1/me/stats", headers=auth)
    client.get("/api/v1/ruta-que-no-existe-123")
    body = client.get("/metrics").text
    assert '# TYPE jh_http_requests_total counter' in body
    assert 'jh_http_requests_total{method="GET",path="/api/v1/me/stats",status="200"} 1' in body
    assert 'path="unmatched"' in body and "ruta-que-no-existe" not in body        # cardinalidad acotada
    assert 'jh_http_request_duration_seconds_bucket{method="GET",path="/health",le="+Inf"}' in body
    assert "jh_llm_queue_depth 0" in body and "jh_searches_running 0" in body and "jh_http_in_flight" in body


def test_metrics_token(client, monkeypatch):
    monkeypatch.setattr(settings, "metrics_token", "s3cr3to")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer mal"}).status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer s3cr3to"}).status_code == 200


def test_metrics_reflect_llm_calls():
    c = llm_client.LLMClient(base_url="http://x:11434/v1", model="m", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"message": {"content": "ok"}, "prompt_eval_count": 5, "eval_count": 2})))
    c.complete("s", "u")
    body = metrics.render()
    assert 'jh_llm_tokens_total{kind="prompt",model="m"} 5' in body
    assert 'jh_llm_latency_seconds_count{model="m"} 1' in body


def test_histogram_buckets_cumulative():
    m = Metrics()
    for v in (0.05, 0.2, 7):
        m.observe("h", v, buckets=(0.1, 1, 10))
    out = m.render()
    assert 'h_bucket{le="0.1"} 1' in out and 'h_bucket{le="1"} 2' in out and 'h_bucket{le="10"} 3' in out
    assert 'h_bucket{le="+Inf"} 3' in out and "h_count 3" in out


def test_json_logs_carry_request_and_user_id(client, auth):
    records = []

    class H(logging.Handler):
        def emit(self, rec):
            records.append(obs.JsonFormatter().format(rec))

    h = H()
    logging.getLogger("jobhunter.access").addHandler(h)
    try:
        client.get("/api/v1/me/stats", headers={**auth, "X-Request-ID": "req-abcdef123456"})
    finally:
        logging.getLogger("jobhunter.access").removeHandler(h)
    line = json.loads(records[-1])
    assert line["request_id"] == "req-abcdef123456" and line["user_id"] == 1
    assert line["path"] == "/api/v1/me/stats" and line["status"] == 200 and line["level"] == "INFO"


def test_json_formatter_includes_exception():
    try:
        raise ValueError("x")
    except ValueError:
        import sys
        rec = logging.LogRecord("n", logging.ERROR, "f", 1, "falló %s", ("algo",), sys.exc_info())
    out = json.loads(obs.JsonFormatter().format(rec))
    assert out["msg"] == "falló algo" and "ValueError" in out["exc"]
