"""Soporte de los tests de IA: servidor LLM simulado (MockTransport) para los tres backends y utilidades de modo."""
from __future__ import annotations

import json

import httpx
from pydantic import SecretStr

from app.config import settings
from tests.fakes import FakeLLM

KEY = "sk-test-SECRET-1234567890abcdef"


class MockProvider:
    """Habla como Ollama (/api/chat), OpenAI-compatible (/chat/completions) o Anthropic (/v1/messages) y guarda lo que recibe.
    `brain` decide la respuesta JSON (por defecto el FakeLLM de los tests); `script` permite forzar códigos HTTP por turno."""

    def __init__(self, brain=None):
        self.brain = brain or FakeLLM()
        self.requests: list[dict] = []          # {"url", "headers", "body", "system", "user"}
        self.script: list[httpx.Response] = []  # respuestas forzadas (se consumen primero)

    @property
    def prompts(self) -> str:
        """Todo el texto que salió hacia el proveedor (system + user de cada petición)."""
        return "\n".join(f"{r['system']}\n{r['user']}" for r in self.requests)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        path = request.url.path
        if path.endswith("/messages"):
            system, user = body["system"], body["messages"][0]["content"]
        else:
            msgs = {m["role"]: m["content"] for m in body["messages"]}
            system, user = msgs["system"], msgs["user"]
        self.requests.append({"url": str(request.url), "headers": dict(request.headers), "body": body, "system": system, "user": user})
        if self.script:
            return self.script.pop(0)
        text = json.dumps(self.brain.complete_json(system, user), ensure_ascii=False)
        tin, tout = max(1, (len(system) + len(user)) // 4), max(1, len(text) // 4)
        if path.endswith("/messages"):
            return httpx.Response(200, json={"content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": text}],
                                             "stop_reason": "end_turn", "usage": {"input_tokens": tin, "output_tokens": tout}})
        if path.endswith("/api/chat"):
            return httpx.Response(200, json={"message": {"role": "assistant", "content": text}, "prompt_eval_count": tin, "eval_count": tout})
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": tin, "completion_tokens": tout}})


def set_mode(monkeypatch, mode: str, provider: str = "openai", mock: MockProvider | None = None, consent_required: bool = True) -> MockProvider:
    """Configura el despliegue en uno de los tres modos. Devuelve el servidor simulado (para inspeccionar peticiones)."""
    from app.api import health
    from app.llm import factory
    health._cache.clear()                       # /ready cachea 5 s la sonda del LLM
    mock = mock or MockProvider()
    monkeypatch.setattr(settings, "ai_mode", mode)
    monkeypatch.setattr(settings, "llm_backoff_s", 0.0)
    monkeypatch.setattr(settings, "llm_api_provider", provider)
    monkeypatch.setattr(settings, "llm_api_base_url", "https://llm.test/v1" if provider == "openai" else "https://anthropic.test")
    monkeypatch.setattr(settings, "llm_api_key", SecretStr(KEY if mode == "api" else ""))
    monkeypatch.setattr(settings, "ai_require_consent", consent_required)
    monkeypatch.setattr(factory, "transport_override", httpx.MockTransport(mock) if mode == "api" else None)
    monkeypatch.setattr(factory, "local_transport_override", httpx.MockTransport(mock) if mode == "local" else None)
    return mock
