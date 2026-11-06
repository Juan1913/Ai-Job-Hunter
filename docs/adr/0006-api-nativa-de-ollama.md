# 0006 — API nativa de Ollama en lugar de la compatible con OpenAI

**Contexto.** `/v1/chat/completions` de Ollama ignora `num_ctx` y `keep_alive`; sin ellos el contexto por defecto
trunca CVs largos o recarga el modelo tras 5 minutos (decenas de segundos en CPU).

**Decisión.** Backend por defecto `ollama` (`/api/chat`: `format: json`, `options.num_ctx/num_predict`, `keep_alive`,
contadores de tokens). `JH_LLM_BACKEND=openai` mantiene la compatibilidad con llama.cpp u otros servidores. El JSON
nativo reduce, pero no elimina, las respuestas rotas de modelos de 3B: de ahí `jsonrepair` y los reintentos acotados.
