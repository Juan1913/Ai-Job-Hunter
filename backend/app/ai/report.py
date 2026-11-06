"""`ai_used` honesto: cada operación informa si usó IA y, si no, por qué (modo none, sin consentimiento, presupuesto, caída)."""
from __future__ import annotations

from app.ai.access import reason_message
from app.ai.mode import ai_available, ai_mode

EXTRACTIVE_MARK = "[sin-ia]"          # primera línea de la traza de un CV generado sin IA


def llm_enabled(llm) -> bool:
    """¿Se puede llamar a este LLM? Vale para GuardedLLM y para dobles de test (que dependen solo del modo)."""
    if llm is None:
        return False
    en = getattr(llm, "enabled", None)
    return en if isinstance(en, bool) else ai_available()


def usable(llm):
    """El LLM si está habilitado; si no, None (la ruta determinista)."""
    return llm if llm_enabled(llm) else None


def disabled_reason(*llms) -> str | None:
    for llm in llms:
        if llm is None:
            continue
        acc = getattr(llm, "access", None)
        if acc is not None and not acc.enabled:
            return acc.reason
    return None if ai_available() else "mode_none"


def _calls(llm) -> int | None:
    c = getattr(llm, "calls", None)
    return c if (hasattr(llm, "access") and isinstance(c, int)) else None


class AiRun:
    """Mide cuántas llamadas reales hizo una operación. Uso: `run = AiRun(llm, writer)` ... `run.report()`."""

    def __init__(self, *llms) -> None:
        self.llms = [x for x in llms if x is not None]
        self.before = [_calls(x) for x in self.llms]
        self.fallback: str | None = None

    def fell_back(self, reason: str = "llm_unavailable") -> None:
        self.fallback = self.fallback or reason

    def report(self) -> dict:
        reason = self.fallback or disabled_reason(*self.llms)
        if reason is None and any(not llm_enabled(x) for x in self.llms):
            reason = disabled_reason(*self.llms) or "llm_unavailable"
        used = reason is None
        if used:
            known = [(b, _calls(x)) for x, b in zip(self.llms, self.before, strict=True) if b is not None]
            if known and all(a == b for b, a in known):
                used, reason = False, "not_needed"
        # un LLM que se agotó a mitad de la operación
        for x in self.llms:
            acc = getattr(x, "access", None)
            if used and acc is not None and not acc.enabled and acc.reason == "budget_exhausted":
                reason = "budget_exhausted"
        return {"ai_used": used, "ai_reason": reason, "ai_message": reason_message(reason), "ai_mode": ai_mode()}


def ai_from_trace(trace: list[str], mode_reason: str | None = None) -> dict:
    """Para CVs ya guardados: ¿se generó sin IA? (marca en la traza)."""
    extractive = any(t.startswith(EXTRACTIVE_MARK) for t in trace)
    reason = None
    if extractive:
        for t in trace:
            if t.startswith(EXTRACTIVE_MARK) and "motivo=" in t:
                reason = t.split("motivo=", 1)[1].split()[0].strip(";,.")
    return {"ai_used": not extractive, "ai_reason": reason if extractive else None,
            "ai_message": reason_message(reason) if extractive else None, "ai_mode": ai_mode()}
