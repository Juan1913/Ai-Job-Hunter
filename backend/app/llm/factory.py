"""Fábrica de clientes LLM según el modo de IA del despliegue (JH_AI_MODE) y el usuario."""
from __future__ import annotations

import httpx

from app.ai import budget, provider
from app.ai.access import AiAccess, access_for
from app.ai.mode import ai_mode
from app.config import settings
from app.llm.client import LLMClient
from app.llm.guard import GuardedLLM

# Solo para pruebas: transporte httpx simulado (MockTransport) para el modo api.
transport_override: httpx.BaseTransport | None = None
local_transport_override: httpx.BaseTransport | None = None


def _client(role: str, user_id: int | None) -> LLMClient:
    if ai_mode() == "local":
        model = settings.llm_model_writer if role == "writer" else settings.llm_model_fast
        return LLMClient(model=model, transport=local_transport_override)
    prov = provider.provider()
    p_in, p_out = provider.prices(role)
    pname = prov

    def on_usage(model: str, tin: int, tout: int) -> None:
        budget.record(user_id, model, pname, tin, tout, budget.cost_usd(p_in, p_out, tin, tout))

    return LLMClient(base_url=provider.base_url(), model=provider.model(role), backend=prov, transport=transport_override,
                     api_key=provider.api_key(), headers=provider.extra_headers(), external=True, on_usage=on_usage,
                     retries=settings.llm_api_retries, timeout_s=settings.llm_api_timeout_s)


def build_llm(role: str = "fast", user_id: int | None = None, access: AiAccess | None = None) -> GuardedLLM:
    """role: 'fast' (extracción/clasificación) | 'writer' (redacción). Siempre devuelve un GuardedLLM; si la IA no
    está disponible para el usuario, `.enabled` es False y las llamadas lanzan LLMDisabled."""
    access = access or access_for(user_id)
    inner = _client(role, user_id) if access.enabled else None
    return GuardedLLM(inner, access, user_id, role)
