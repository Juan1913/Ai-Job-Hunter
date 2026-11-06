"""Envoltorio por usuario: aplica consentimiento/presupuesto y cuenta llamadas reales (para `ai_used`)."""
from __future__ import annotations

from app.ai import budget
from app.ai.access import AiAccess
from app.llm.client import LLMClient, LLMDisabled


class GuardedLLM:
    """Misma interfaz que LLMClient (complete / complete_json). Si la IA no está disponible para el usuario,
    cada llamada lanza `LLMDisabled` (un LLMUnavailable): los consumidores ya saben degradar a su ruta determinista."""

    def __init__(self, inner: LLMClient | None, access: AiAccess, user_id: int | None = None, role: str = "fast"):
        self.inner, self.access, self.user_id, self.role = inner, access, user_id, role
        self.calls = 0

    @property
    def enabled(self) -> bool:
        return self.access.enabled and self.inner is not None

    def _check(self) -> LLMClient:
        if not self.enabled:
            raise LLMDisabled(f"IA no disponible: {self.access.message or 'desactivada'}", self.access.reason or "mode_none")
        if self.access.mode == "api":                    # el presupuesto puede agotarse a mitad de una operación
            st = budget.state(self.user_id)
            if st.exhausted:
                self.access = AiAccess(False, "budget_exhausted", self.access.mode, True)
                raise LLMDisabled(f"IA no disponible: {st.message}", "budget_exhausted")
        return self.inner  # type: ignore[return-value]

    def complete(self, system: str, user: str, temperature: float = 0.1, **kw) -> str:
        out = self._check().complete(system, user, temperature, **kw)
        self.calls += 1
        return out

    def complete_json(self, system: str, user: str, **kw):
        out = self._check().complete_json(system, user, **kw)
        self.calls += 1
        return out

    def __getattr__(self, name: str):          # model, warmup, close...
        inner = self.__dict__.get("inner")
        if inner is None:
            raise AttributeError(name)
        return getattr(inner, name)
