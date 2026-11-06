# 0007 — Tres modos de IA: local, api y none

**Contexto.** El sistema debe funcionar 24 h y el operador decide cómo se usa la IA: un modelo pequeño en CPU (lento, sin
salida de datos), una API externa (rápida, pero los CVs y ofertas salen a un tercero) o ninguna. Nada debe romperse ni
devolver 502 por la elección.

**Decisión.** `JH_AI_MODE=local|api|none` (`app/ai/mode.py` es el único contrato que el resto consulta: `ai_available()`).
- **Proveedores** (`app/llm`): `LLMClient` con backends `ollama`, `openai` (OpenAI-compatible: OpenAI, Groq, OpenRouter, Gemini
  compat...) y `anthropic` (Messages API vía httpx; sin `temperature`, JSON por instrucción + reparación). Mismo endurecimiento
  (reintentos, backoff, `Retry-After` con tope, reparación de JSON, errores tipificados). El modo api tiene su propia cola
  (`JH_LLM_API_MAX_CONCURRENCY`, el proveedor sí admite paralelismo) y el local conserva la cola justa de 1.
- **Acceso por usuario** (`app/ai/access.py`): el modo `api` exige clave configurada, consentimiento explícito (`AiConsent`) y
  presupuesto diario (`LLMUsage`: usuario, día UTC, modelo, tokens, coste estimado). `GuardedLLM` envuelve al cliente y lanza
  `LLMDisabled` si no procede (también si el presupuesto se agota a mitad de una operación).
- **Degradación**: sin IA cada paso tiene su ruta determinista (extractor de requisitos, clasificación con evidencia literal,
  CV extractivo, carta de plantilla). Los endpoints devuelven `ai: {ai_used, ai_reason, ai_message}`; un fallo del proveedor
  también degrada en vez de dar 502.
- **Privacidad** (`app/ai/privacy.py`): los prompts se construyen sin email/teléfono/ubicación/nombre y, como última barrera, el
  cliente externo sustituye contacto, URLs y direcciones por marcadores que reinyecta en la respuesta. La clave solo vive en el
  entorno y se redacta en errores, logs y `/ready`. `GET /api/v1/ai/status` lista qué sale y qué no.

**Consecuencias.** `none` mantiene cobertura ATS comparable (medida en 5 profesiones, ver README) pero no reescribe ni traduce, y
ADJACENT es más conservador. En `api` la privacidad depende del proveedor elegido; el nombre y el cuerpo de la oferta salen
cuando el usuario consiente. Los tokens son estimaciones del proveedor; el coste exige tarifa configurada. El presupuesto y la
cola del modo api son en memoria/BD de una sola instancia (ADR 0001).
