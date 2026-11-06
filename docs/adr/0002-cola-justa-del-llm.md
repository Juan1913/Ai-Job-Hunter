# 0002 — Cola global y justa para el LLM

**Contexto.** Dos inferencias simultáneas en CPU no van el doble de rápido: cada una tarda más y puede agotar la RAM.
Con varios usuarios, un FIFO estricto deja que quien lanza 10 análisis seguidos retrase a todos.

**Decisión.** `FairGate` (`app/llm/client.py`): semáforo de `JH_LLM_MAX_CONCURRENCY` (1) con una cola FIFO por usuario y
round-robin entre usuarios. La clave es el `user_id` del contexto de la petición (`"system"` en tareas de fondo). La
espera tiene tope (`JH_LLM_QUEUE_TIMEOUT_S`) y termina en `LLMBusy`. Ollama se configura con `OLLAMA_NUM_PARALLEL=1`.

**Consecuencias.** Latencia predecible y justicia entre usuarios; la cola es observable (`jh_llm_queue_depth`,
`jh_llm_queue_wait_seconds`). Los reintentos retienen el turno (simplicidad frente a reencolar).
