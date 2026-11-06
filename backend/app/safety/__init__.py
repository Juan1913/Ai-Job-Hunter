"""Seguridad de entrada: portón de integridad para texto ajeno que llega a los prompts (ver integrity.py)."""
from app.safety.integrity import (  # noqa: F401
    CLOSE_MARK,
    OPEN_MARK,
    UNTRUSTED_RULE,
    Finding,
    IntegrityResult,
    gate_job,
    inspect,
    offer_block,
    sanitize_for_llm,
    untrusted_block,
)
