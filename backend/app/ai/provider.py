"""Configuración del proveedor en modo api (sin exponer nunca la clave)."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from app.config import settings

_DEFAULTS = {
    "openai": {"base": "https://api.openai.com/v1", "fast": "gpt-4o-mini", "writer": "gpt-4o"},
    "anthropic": {"base": "https://api.anthropic.com", "fast": "claude-haiku-5-5", "writer": "claude-sonnet-5-5"},
}


def provider() -> str:
    return "anthropic" if settings.llm_api_provider.strip().lower().startswith("anthropic") else "openai"


def api_key() -> str:
    key = settings.llm_api_key.get_secret_value().strip()
    if not key and settings.llm_api_key_file:
        try:
            key = Path(settings.llm_api_key_file).read_text().strip()
        except OSError:
            key = ""
    return key


def base_url() -> str:
    return (settings.llm_api_base_url.strip() or _DEFAULTS[provider()]["base"]).rstrip("/")


def model(role: str) -> str:
    configured = settings.llm_api_model_writer if role == "writer" else settings.llm_api_model_fast
    return configured.strip() or _DEFAULTS[provider()]["writer" if role == "writer" else "fast"]


def prices(role: str) -> tuple[float, float]:
    return ((settings.llm_api_price_writer_in, settings.llm_api_price_writer_out) if role == "writer"
            else (settings.llm_api_price_fast_in, settings.llm_api_price_fast_out))


def configured() -> bool:
    return bool(api_key())


def extra_headers() -> dict[str, str]:
    out: dict[str, str] = {}
    for part in settings.llm_api_extra_headers.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out


def public_info() -> dict:
    """Lo que puede verse en /ai/status y /ready: proveedor, host y modelos. Nunca la clave."""
    return {"provider": provider(), "host": urlparse(base_url()).hostname or "", "model_fast": model("fast"),
            "model_writer": model("writer"), "key_configured": configured()}
