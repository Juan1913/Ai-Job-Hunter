"""Privacidad en modo `api`: qué sale al proveedor y cómo se minimiza.

Dos defensas, ambas locales:
1. En el origen: los prompts de CV/carta se construyen con `profile_for_prompt` (sin email, teléfono, ubicación ni nombre).
2. En el cliente HTTP (última barrera): `Redactor` sustituye email, teléfono, URL/perfiles sociales y direcciones postales
   de CUALQUIER texto saliente por marcadores ([EMAIL_1]...) y, si el modelo los repite, los reinyecta localmente.

`SENT_TO_PROVIDER` / `NEVER_SENT` alimentan el aviso de privacidad del panel (GET /api/v1/ai/status)."""
from __future__ import annotations

import re

from app.domain.profile import Profile

_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_URL = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"')\]]+|\b(?:[a-z]{2,3}\.)?(?:linkedin|github|gitlab|behance|twitter|facebook|instagram)\.com/[^\s<>\"')\]]*")
_PHONE = re.compile(r"(?<![\w/@])\+?\(?\d[\d\s().\-]{6,}\d(?![\w@])")
_ADDR = re.compile(r"(?i)\b(?:calle|carrera|cra\.?|cll\.?|av\.?|avenida|diagonal|transversal|autopista|street|st\.|road|rd\.|avenue|ave\.?|boulevard|blvd|lane|drive)"
                   r"\s+(?:[\wáéíóúñ.]+\s+){0,3}?\d+[a-z]?(?:\s*(?:#|nº|no\.)\s*\d+[a-z]?(?:\s*-\s*\d+)?|\s*-\s*\d+)?(?:,\s*\d+[a-z]?\b)?")
_YEAR = re.compile(r"^(?:19|20)\d{2}$")
_SECRET = re.compile(r"(?i)\b(?:sk|gsk|or|key|pk|rk)[-_][A-Za-z0-9_\-]{12,}|\bBearer\s+[A-Za-z0-9._\-]{12,}|AIza[0-9A-Za-z_\-]{20,}")

SENT_TO_PROVIDER = [
    "Texto de la oferta (título, empresa, descripción saneada) para extraer requisitos y redactar",
    "Habilidades, cargos, empresas, fechas y viñetas de tu perfil (solo en las funciones que usan IA)",
    "Fragmentos literales de tu perfil como evidencia al clasificar brechas",
    "Texto del CV subido SOLO cuando las reglas no bastan (cabecera para identificar nombre/cargo, habilidades, puestos ambiguos)",
]
NEVER_SENT = [
    "Email, teléfono, dirección postal, ubicación y enlaces personales (se sustituyen por marcadores y se reinyectan en local)",
    "Contraseñas, tokens, preferencias de búsqueda, vetos salariales y estado de tus candidaturas",
    "Tu nombre en las funciones de CV y carta (la firma se añade en local)",
    "La clave del proveedor: vive solo en el entorno del servidor",
]


def _digits(s: str) -> int:
    return sum(c.isdigit() for c in s)


def _is_phone(m: str) -> bool:
    d = _digits(m)
    groups = re.findall(r"\d+", m)
    if all(_YEAR.match(g) for g in groups):          # '2015 - 2017' no es un teléfono
        return False
    return d >= 9 or (m.lstrip().startswith("+") and d >= 8)


class Redactor:
    """Sustituye datos de contacto por marcadores estables dentro de una misma petición y los reinyecta en la respuesta."""

    def __init__(self) -> None:
        self.map: dict[str, str] = {}        # marcador -> original
        self._rev: dict[str, str] = {}       # original -> marcador
        self._n: dict[str, int] = {}

    def _token(self, kind: str, original: str) -> str:
        if original in self._rev:
            return self._rev[original]
        self._n[kind] = self._n.get(kind, 0) + 1
        tok = f"[{kind}_{self._n[kind]}]"
        self.map[tok], self._rev[original] = original, tok
        return tok

    def apply(self, text: str) -> str:
        if not text:
            return text
        text = _EMAIL.sub(lambda m: self._token("EMAIL", m.group(0)), text)
        text = _URL.sub(lambda m: self._token("URL", m.group(0)), text)
        text = _ADDR.sub(lambda m: self._token("DIRECCION", m.group(0).strip()), text)
        return _PHONE.sub(lambda m: self._token("TELEFONO", m.group(0).strip()) if _is_phone(m.group(0)) else m.group(0), text)

    def restore(self, text: str) -> str:
        for tok, original in self.map.items():
            text = text.replace(tok, original)
        return text


def scrub(text: str) -> str:
    """Una sola pasada sin reinyección (para trazas y pruebas)."""
    return Redactor().apply(text)


def redact_secrets(text: str, *known: str) -> str:
    """Quita claves de mensajes de error/logs: la clave configurada y patrones habituales (sk-..., Bearer ...)."""
    for k in known:
        if k and len(k) >= 6:
            text = text.replace(k, "***")
    return _SECRET.sub("***", text)


def profile_for_prompt(profile: Profile) -> dict:
    """Perfil tal como se envía a un LLM: sin email, teléfono, ubicación, nombre ni preferencias."""
    return profile.model_dump(exclude={"preferences", "email", "phone", "location", "full_name"})
