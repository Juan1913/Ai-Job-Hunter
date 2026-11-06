# 0008 — Trabajador 24/7 con cola persistente en BD (sustituye a 0001 en lo de «un solo proceso»)

**Estado:** aceptada

**Contexto.** El producto debe estar funcionando 24 h: buscar, evaluar y avisar solo. Con búsquedas a mano y tareas lentas en hilos
dentro de la API (ADR 0001) un reinicio perdía el trabajo en curso, nada ocurría si nadie abría el panel, y la API competía por CPU
con el scraping. Redis o un broker añaden un servicio más para un sistema de decenas de usuarios.

**Decisión.**
- `python -m app.worker`: proceso aparte, misma imagen. La cola es la tabla `workitem` (estado, prioridad, intentos, `run_after`,
  lease `locked_by`/`locked_until`, dead-letter). Reclamar = `SELECT ... FOR UPDATE SKIP LOCKED` en PostgreSQL y `BEGIN IMMEDIATE`
  en SQLite; cada transición posterior es un `UPDATE` condicionado al dueño del lease, así un trabajador que lo perdió no pisa a
  quien lo recuperó. El trabajador renueva el lease mientras vive; si cae, otro lo recupera (`recover_orphans`). Reintento con
  backoff exponencial y jitter; agotados los intentos, `dead`. Apagado ordenado con SIGTERM (devuelve lo no terminado a la cola
  sin gastar intento). Latido en `workerheartbeat`; `/ready` lo exige solo con `JH_WORKER_EXTERNAL=true`; el healthcheck del
  contenedor es `python -m app.worker --healthcheck`.
- Con `JH_WORKER_EXTERNAL=true` la API encola (búsquedas, tareas) y no ejecuta nada pesado. Con `JH_RUN_INLINE` o sin
  trabajador externo todo se comporta como antes (hilos / mismo hilo): compatibilidad total.
- Las búsquedas programadas (`savedsearch`) las dispara el bucle del trabajador con un compare-and-set sobre `next_run_at`
  (varios trabajadores no duplican). Cortesía: intervalo mínimo configurable, jitter, ventana horaria, backoff exponencial por
  fallos y pausa automática tras N fallos seguidos con aviso.
- Caché compartida entre usuarios de lo que devolvió cada portal (clave: versión + fuente + país + hash de la consulta normalizada
  y filtros; TTL; nunca se guarda lo bloqueado) y presupuesto de peticiones por hora (global y por dominio) contado sobre el rate
  limiter existente sin editar `app/scout`. La evaluación y el puntaje siguen siendo siempre por usuario.
- Avisos: tabla `notification` (dedup por usuario), canales opcionales (webhook firmado HMAC, ntfy, Telegram, SMTP del operador)
  con entregas en la misma cola (reintentos, dead-letter, límite por hora, silencio nocturno por zona horaria, resumen
  diario/semanal). Secretos cifrados en reposo; SSRF-guard al guardar y al enviar.

**SQLite o PostgreSQL con varios procesos (decisión con evidencia).**
- Medido aquí (4 procesos Python reclamando 400 trabajos a la vez y completándolos): SQLite WAL 400/400 únicos, 0 duplicados,
  0 reintentos por bloqueo, ~170 trabajos/s; PostgreSQL 16 (`SKIP LOCKED`) 400/400, 0 duplicados, ~130-150 trabajos/s con 4 y 8 procesos.
  La cola no es el cuello de botella en ninguno (el sistema mueve decenas de trabajos por hora).
- API + trabajador sobre el mismo archivo SQLite WAL **es seguro si ambos procesos corren en el MISMO host y ven el volumen
  como disco local** (WAL usa memoria compartida mapeada del archivo `-shm`; dos contenedores con el mismo volumen Docker en un
  host la comparten). **No** lo es sobre sistemas de archivos de red (NFS/SMB/EFS) ni entre hosts distintos.
- Límites reales de SQLite: un solo escritor a la vez (las transacciones largas de uno bloquean a los demás hasta `busy_timeout`,
  30 s aquí) y los trabajadores deben ser pocos. Por eso: **SQLite = un host, API + 1 trabajador, decenas de usuarios** (el
  valor por defecto); **PostgreSQL = varios trabajadores o hosts, cientos de usuarios, o cualquier volumen que no sea disco local**.
  Pulumi usa SQLite por defecto y exige `database=postgres` si `workerReplicas` > 1.

**Consecuencias.** Un contenedor más (mismo tamaño de imagen, solo vive el trabajador). El límite de tasa, el bloqueo de login y
la cola del LLM de la API siguen en memoria de la API; el trabajador tiene su propia cola de LLM (`JH_LLM_MAX_CONCURRENCY` por
proceso), así que con API y trabajador la CPU de inferencia puede recibir dos inferencias a la vez: en CPU conviene dejar
`JH_WORKER_CONCURRENCY` bajo. El presupuesto de peticiones aprende qué dominios usa cada fuente en la primera ejecución de cada
proceso: la primera vez solo manda el tope global. Los DNS rebinding contra webhooks quedan mitigados (se revisa justo antes de
enviar y no se siguen redirecciones) pero no eliminados: un resolutor malicioso podría cambiar entre la comprobación y la conexión.
