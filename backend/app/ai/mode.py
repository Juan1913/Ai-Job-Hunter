"""Modo de IA del despliegue. Contrato mínimo: el resto del sistema solo pregunta esto.

  local : modelo en esta máquina (Ollama), sin salida de datos.
  api   : API externa (OpenAI-compatible o Anthropic): los textos del usuario SALEN a un tercero.
  none  : sin IA. Todo corre con reglas, léxico y embeddings por hashing; el CV se adapta de forma extractiva.
"""
from app.config import settings

MODES = ("local", "api", "none")


def ai_mode() -> str:
    m = (settings.ai_mode or "local").lower()
    return m if m in MODES else "local"


def ai_available() -> bool:
    """False => los pasos que dependen de LLM deben usar su alternativa determinista."""
    return ai_mode() != "none"


def sends_data_to_third_party() -> bool:
    return ai_mode() == "api"
