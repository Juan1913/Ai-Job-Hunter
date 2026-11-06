"""¿Puede ESTE usuario usar IA ahora mismo? Combina modo del operador, configuración, consentimiento y presupuesto."""
from __future__ import annotations

from dataclasses import dataclass

from app.ai import budget, consent, provider
from app.ai.mode import ai_mode, sends_data_to_third_party

REASONS = {
    "mode_none": "El operador desplegó JobHunter sin IA: todo funciona con reglas y extracción literal de tu perfil.",
    "no_consent": "Aún no autorizaste el envío de tus textos a un proveedor externo de IA: se usa la ruta sin IA.",
    "budget_exhausted": "Se agotó el presupuesto diario de IA: se usa la ruta sin IA hasta mañana.",
    "not_configured": "El operador activó la IA por API pero falta la clave del proveedor: se usa la ruta sin IA.",
    "llm_unavailable": "El modelo de IA no respondió: se usó la ruta sin IA.",
    "validation_failed": "El modelo no pasó las guardias anti-invención: se usó la versión determinista (solo datos de tu perfil).",
    "deterministic": "Esta parte es determinista por diseño (reglas sobre tu perfil y la oferta).",
    "not_needed": "Esta operación no necesitó llamar al modelo.",
}


@dataclass(frozen=True)
class AiAccess:
    enabled: bool
    reason: str | None = None          # mode_none | no_consent | budget_exhausted | not_configured
    mode: str = "local"
    sends_data: bool = False

    @property
    def message(self) -> str | None:
        return REASONS.get(self.reason or "")


def reason_message(reason: str | None) -> str | None:
    return REASONS.get(reason or "")


def access_for(user_id: int | None) -> AiAccess:
    mode = ai_mode()
    if mode == "none":
        return AiAccess(False, "mode_none", mode)
    if mode == "local":
        return AiAccess(True, None, mode, False)
    # api
    if not provider.configured():
        return AiAccess(False, "not_configured", mode, True)
    from app.config import settings
    if settings.ai_require_consent and (user_id is None or not consent.has_consent(user_id)):
        return AiAccess(False, "no_consent", mode, True)
    if budget.state(user_id).exhausted:
        return AiAccess(False, "budget_exhausted", mode, True)
    return AiAccess(True, None, mode, sends_data_to_third_party())
