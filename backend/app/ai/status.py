"""Estado de la IA para el panel: modo, si los datos salen a un tercero, proveedor (sin clave), capacidades por función."""
from __future__ import annotations

from app.ai import budget, provider
from app.ai.access import AiAccess, access_for
from app.ai.mode import ai_mode, sends_data_to_third_party
from app.ai.privacy import NEVER_SENT, SENT_TO_PROVIDER
from app.config import settings

FUNCTIONS = ("cv_reading", "requirements", "gaps", "cv_writing", "cover_letter", "prep", "evaluation")

# Método cuando la IA está disponible. 'rules+llm' = reglas primero y LLM solo donde hay ambigüedad, con verificación posterior.
_WITH_AI = {"cv_reading": "rules+llm", "requirements": "llm", "gaps": "llm", "cv_writing": "llm", "cover_letter": "llm",
            "prep": "deterministic", "evaluation": "deterministic"}
_WITHOUT_AI = {"cv_reading": "rules", "requirements": "lexicon+patterns+tfidf", "gaps": "rules+local-embeddings", "cv_writing": "extractive",
               "cover_letter": "template", "prep": "deterministic", "evaluation": "deterministic"}
_OPTIONAL_LLM = {"prep": "preguntas extra opcionales (use_llm)", "evaluation": "refinado opcional de años/nivel/idiomas"}


def capabilities(access: AiAccess) -> dict[str, dict]:
    out = {}
    for f in FUNCTIONS:
        ai = access.enabled and _WITH_AI[f] != "deterministic"
        out[f] = {"ai_active": ai, "method": _WITH_AI[f] if ai else _WITHOUT_AI[f],
                  "sends_data": bool(ai and access.sends_data),
                  **({"optional_llm": _OPTIONAL_LLM[f]} if f in _OPTIONAL_LLM and access.enabled else {})}
    return out


def public_status() -> dict:
    mode = ai_mode()
    access = access_for(None) if mode != "api" else AiAccess(provider.configured(), None if provider.configured() else "not_configured", mode, True)
    info: dict = {"mode": mode, "sends_data_to_third_party": sends_data_to_third_party(), "ai_available": mode != "none"}
    if mode == "local":
        info["provider"] = {"provider": "ollama", "host": "local", "model_fast": settings.llm_model_fast, "model_writer": settings.llm_model_writer}
    elif mode == "api":
        info["provider"] = provider.public_info()
        st = budget.state(None)
        info["budget_exhausted"] = bool(st.exhausted and st.scope == "global")
        info["consent_required"] = settings.ai_require_consent
        info["privacy"] = {"version": settings.ai_privacy_version, "sent": SENT_TO_PROVIDER, "never_sent": NEVER_SENT}
    else:
        info["provider"] = None
    info["capabilities"] = capabilities(access)
    return info


def user_status(user_id: int) -> dict:
    mode = ai_mode()
    access = access_for(user_id)
    out = public_status()
    out["capabilities"] = capabilities(access)
    out["you"] = {"ai_enabled": access.enabled, "reason": access.reason, "message": access.message}
    if mode == "api":
        from app.ai.consent import has_consent
        st = budget.state(user_id)
        out["you"].update({"consent": has_consent(user_id), "budget": {"used": st.user, "limits": st.limits, "exhausted": st.exhausted,
                                                                       "scope": st.scope, "message": st.message}})
        out["budget_exhausted"] = bool(st.exhausted and st.scope == "global")
    return out
