# 0001 — Un solo proceso backend (sin worker aparte)

**Estado:** aceptada; en lo de «sin worker aparte» la reemplaza la [0008](0008-trabajador-24-7-cola-en-bd.md) (la API sigue siendo un solo proceso)

**Contexto.** Las búsquedas son en segundo plano y el LLM corre en CPU, donde solo cabe una inferencia a la vez.
Un worker separado exigiría una cola persistente (tabla o Redis), coordinación de la cola del LLM entre procesos y otro
contenedor con la imagen de ~1,7 GB (Chromium).

**Decisión.** API y trabajos de fondo comparten proceso (`ThreadPoolExecutor`, 2 hilos de búsqueda). Un único contenedor
`backend`, un único worker de uvicorn. Rate limits, bloqueo de login y cola del LLM son estructuras en memoria.

**Consecuencias.** Simple y suficiente para decenas de usuarios sobre una sola CPU de inferencia. Un reinicio pierde
búsquedas en curso (quedan `running`) y contadores en memoria. Si se necesita escalar: mover los trabajos a una tabla
de cola + worker (misma imagen, otro comando) y los límites a Redis; el cliente LLM ya está aislado en `app/llm`.
