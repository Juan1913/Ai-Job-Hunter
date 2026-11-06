from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET = "dev-only-change-me-dev-only-change-me-0000"


class Settings(BaseSettings):
    # Todas las variables se leen con prefijo JH_ (p. ej. JH_LLM_NUM_CTX).
    model_config = SettingsConfigDict(env_prefix="JH_", env_file=".env", extra="ignore")

    env: str = "dev"                       # "prod" exige JH_JWT_SECRET propio
    log_level: str = "INFO"
    log_json: bool = True                  # logs JSON estructurados (False = texto legible)
    run_inline: bool = False               # tests: ejecutar tareas en el mismo hilo
    max_cv_bytes: int = 5 * 1024 * 1024

    # --- Base de datos ---
    database_url: str = "sqlite:///./jobhunter.db"   # también acepta postgresql+psycopg://...
    # En dev/tests la app crea las tablas sola; en producción las crea Alembic (entrypoint).
    db_auto_create: bool = True

    # --- Autenticación ---
    jwt_secret: str = _DEV_SECRET
    jwt_ttl_minutes: int = 30              # access token (corto: se renueva con el refresh token)
    refresh_ttl_days: int = 14             # refresh token rotatorio
    login_max_failures: int = 5            # fallos antes del bloqueo temporal
    login_lock_s: int = 15 * 60
    login_fail_window_s: int = 15 * 60

    # --- Seguridad HTTP ---
    cors_origins: str = ""                 # lista separada por comas; "*" no se admite con credenciales
    max_body_bytes: int = 8 * 1024 * 1024  # tope global de body (CV + sobrecarga multipart)
    security_headers: bool = True
    metrics_token: str = ""                # si se define, /metrics exige "Authorization: Bearer <token>"
    hsts: bool = False                     # activar solo detrás de TLS (el proxy ya suele ponerlo)
    trust_proxy_headers: bool = False      # confiar en X-Forwarded-For (solo tras un proxy propio)

    # --- Rate limiting (token bucket en memoria; rate = peticiones por minuto) ---
    rate_limit_enabled: bool = True
    rate_ip_per_min: int = 240
    rate_ip_burst: int = 120
    rate_user_per_min: int = 120
    rate_user_burst: int = 60
    rate_auth_per_min: int = 20            # /auth/*: más estricto, por IP
    rate_auth_burst: int = 10
    # Cuotas diarias por usuario (0 = sin límite). Protegen la CPU compartida.
    quota_searches_per_day: int = 20
    quota_tailor_per_day: int = 30
    quota_analyze_per_day: int = 200

    # --- LLM (Ollama / servidor compatible con OpenAI) ---
    ai_mode: str = "local"                 # local | api | none  (ver app/ai/mode.py)
    llm_base_url: str = "http://localhost:11434/v1"
    # Modelos fijados por el despliegue (no los elige el usuario):
    # el ligero extrae/puntúa; el de redacción escribe y adapta el CV.
    llm_backend: str = "ollama"            # "ollama" (/api/chat: num_ctx, keep_alive) | "openai" (/chat/completions)
    llm_model_fast: str = "qwen2.5:3b-instruct"
    llm_model_writer: str = "qwen2.5:7b-instruct"
    llm_model_embed: str = "nomic-embed-text"
    llm_timeout_s: float = 180.0           # generación (lectura de la respuesta)
    llm_connect_timeout_s: float = 5.0     # conexión al servidor
    llm_native_json: bool = True           # response_format=json_object (Ollama lo soporta)
    llm_json_retries: int = 2              # reintentos si el JSON viene roto
    llm_retries: int = 2                   # reintentos ante fallo de red/5xx
    llm_backoff_s: float = 1.0             # backoff exponencial base
    llm_max_concurrency: int = 1           # inferencias simultáneas (CPU: 1)
    llm_queue_timeout_s: float = 600.0     # máximo esperando turno en la cola
    llm_num_ctx: int = 4096
    llm_num_predict: int = 1024
    llm_keep_alive: str = "30m"
    llm_warmup: bool = False               # cargar modelos en memoria al arrancar
    ready_requires_llm: bool = True        # /ready exige LLM alcanzable y modelos descargados (solo modo local)

    # --- IA en modo `api` (ver docs/adr/0007-modos-de-ia.md). La clave SOLO por entorno/secreto: nunca en BD ni en logs ---
    llm_api_provider: str = "openai"       # openai (OpenAI-compatible: OpenAI, Groq, OpenRouter, Gemini compat...) | anthropic (Messages API)
    llm_api_base_url: str = ""             # vacío = https://api.openai.com/v1 o https://api.anthropic.com según el proveedor
    llm_api_key: SecretStr = SecretStr("")
    llm_api_key_file: str = ""             # alternativa: ruta de un secreto montado (Docker/K8s); gana si la clave directa está vacía
    llm_api_model_fast: str = ""           # vacío = valor por defecto del proveedor (openai: gpt-4o-mini; anthropic: claude-haiku-5-5)
    llm_api_model_writer: str = ""         # vacío = valor por defecto del proveedor (openai: gpt-4o; anthropic: claude-sonnet-5-5)
    llm_api_max_concurrency: int = 4       # llamadas simultáneas (la API sí admite paralelismo)
    llm_api_timeout_s: float = 90.0
    llm_api_retries: int = 3               # reintentos ante red/5xx/429
    llm_api_retry_after_max_s: float = 30.0  # tope de espera obedeciendo Retry-After del proveedor
    llm_api_anthropic_version: str = "2023-06-01"
    llm_api_anthropic_effort: str = "low"  # output_config.effort de los modelos 5.x (vacío = no enviarlo)
    llm_api_extra_headers: str = ""        # "Cabecera: valor;Otra: valor" (p. ej. HTTP-Referer de OpenRouter); sin secretos
    # Tarifa opcional en USD por millón de tokens (0 = no se estima coste)
    llm_api_price_fast_in: float = 0.0
    llm_api_price_fast_out: float = 0.0
    llm_api_price_writer_in: float = 0.0
    llm_api_price_writer_out: float = 0.0
    # Presupuesto diario en modo api (0 = sin límite). Al agotarse, el usuario cae a la ruta determinista con aviso.
    ai_budget_user_tokens_per_day: int = 200_000
    ai_budget_user_calls_per_day: int = 300
    ai_budget_global_tokens_per_day: int = 2_000_000
    ai_budget_global_calls_per_day: int = 5_000
    ai_budget_global_cost_per_day: float = 0.0   # USD estimados (requiere tarifa)
    ai_require_consent: bool = True        # modo api: sin consentimiento explícito del usuario no sale ningún dato
    ai_privacy_version: str = "1"          # sube el número para pedir consentimiento de nuevo

    # --- RAG / embeddings / matcher ---
    embed_base_url: str = ""               # vacío = usa llm_base_url
    embed_backend: str = "auto"            # auto (Ollama y, si falla, hashing) | ollama | hash
    embed_timeout_s: float = 30.0
    embed_batch: int = 16
    # --- Tree-of-Thought del CV (cada rama = 1 llamada lenta al writer) ---
    tot_branches: int = 1                  # estrategias iniciales; 1 = comportamiento clásico (sin ToT)
    tot_depth: int = 1                     # niveles; el nivel 2+ refina las mejores ramas
    tot_beam: int = 2                      # ramas que sobreviven a cada poda


    # --- Scraping (app/scout) ---
    proxies: str = ""                      # JH_PROXIES: lista (coma/espacio/salto de línea) http(s)://, socks5://, user:pass@host:port, host:port:user:pass
    proxy_cooldown_s: float = 60.0         # retirada inicial de un proxy que falla (se duplica hasta proxy_cooldown_max_s)
    proxy_cooldown_max_s: float = 900.0
    breaker_cooldown_s: float = 30.0       # circuit breaker por dominio tras un 429/bloqueo: cooldown inicial (crece x2)
    breaker_cooldown_max_s: float = 900.0
    scout_llm_expand: bool = False         # expansión de consulta con LLM (cacheada en BD); el diccionario curado funciona sin ella
    # Fuentes con clave gratuita: desactivadas mientras no haya clave.
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    jooble_api_key: str = ""               # la clave gratuita de Jooble es por país y con 500 peticiones de por vida: se usa con presupuesto mínimo
    infojobs_client_id: str = ""           # API oficial de InfoJobs (credenciales de aplicación, no de usuario)
    infojobs_client_secret: str = ""

    # --- Trabajador 24/7 (app/worker): cola persistente en BD, búsquedas programadas, avisos ---
    worker_external: bool = False          # True: la API NO ejecuta tareas lentas en hilos propios; las encola para `python -m app.worker`
    worker_id: str = ""                    # identificador del trabajador (vacío = host-pid)
    worker_concurrency: int = 2            # trabajos simultáneos por trabajador (el LLM serializa sus inferencias aparte)
    worker_poll_s: float = 2.0             # cada cuánto mira la cola y programa búsquedas vencidas
    worker_lease_s: float = 300.0          # un trabajo reclamado es suyo este tiempo; se renueva mientras vive. Vencido => otro lo recupera
    worker_heartbeat_s: float = 10.0       # latido en BD
    worker_alive_s: float = 45.0           # un latido más viejo que esto = trabajador caído (/ready, healthcheck)
    worker_max_attempts: int = 3           # intentos por trabajo antes de ir a dead-letter
    worker_backoff_s: float = 30.0         # reintento: base * 2^(intento-1) con jitter
    worker_backoff_max_s: float = 1800.0
    worker_grace_s: float = 60.0           # al recibir SIGTERM espera esto a los trabajos en curso; luego los devuelve a la cola
    worker_metrics_port: int = 0           # >0: expone /metrics y /health del trabajador en ese puerto
    worker_retention_days: int = 14        # trabajos terminados y avisos leídos más viejos se purgan
    # Búsquedas programadas (app/scheduling)
    schedule_min_interval_min: int = 60    # intervalo mínimo permitido por búsqueda guardada (cortesía con los portales)
    schedule_max_per_user: int = 10
    schedule_jitter: float = 0.15          # +-15 % sobre cada intervalo: evita que todas disparen a la vez
    schedule_max_failures: int = 5         # fallos seguidos => pausa automática con aviso
    schedule_backoff_max_min: int = 24 * 60
    # Caché compartida de resultados crudos de scraping y presupuesto de peticiones
    cache_ttl_s: int = 3600                # vigencia de un resultado (fuente, consulta normalizada, país)
    cache_empty_ttl_s: int = 900           # un resultado vacío caduca antes (puede ser un fallo silencioso)
    cache_ttl_overrides: str = ""          # "linkedin=7200,indeed=7200": TTL por fuente
    cache_manual: bool = False             # True: las búsquedas lanzadas a mano también leen/escriben la caché compartida
    budget_global_per_hour: int = 3000     # peticiones salientes a portales por hora (todo el sistema); 0 = sin tope
    budget_domain_per_hour: int = 300      # ... y por dominio
    # Avisos (app/notifications)
    notify_rate_per_hour: int = 20         # envíos por canal y hora (anti-spam); el resto espera
    notify_max_attempts: int = 5
    notify_backoff_s: float = 60.0
    notify_channel_max_failures: int = 10  # entregas muertas seguidas => el canal se desactiva con aviso
    notify_timeout_s: float = 10.0
    notify_stream_max_s: float = 300.0     # duración máxima de una conexión SSE (el cliente reconecta con Last-Event-ID)
    notify_telegram_api: str = "https://api.telegram.org"
    secrets_key: str = ""                  # clave de cifrado de secretos de canal; vacía = se deriva de JH_JWT_SECRET
    smtp_host: str = ""                    # email de salida (operador); vacío = el canal email no existe
    smtp_port: int = 587
    smtp_user: str = ""
    autoapply_headless: bool = True        # navegador sin ventana al auto-postular (False para verlo trabajar al depurar)
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    # Pre-generación de CV (política por usuario): sin IA solo si existe un modo extractivo, o si el operador lo fuerza
    worker_pregen_without_ai: bool | None = None


settings = Settings()


def validate_production(s: Settings) -> None:
    if s.env == "prod" and (s.jwt_secret == _DEV_SECRET or len(s.jwt_secret) < 32):
        raise RuntimeError("JH_JWT_SECRET debe definirse en producción (mínimo 32 caracteres)")
    if s.env == "prod" and s.cors_origins.strip() == "*":
        raise RuntimeError("JH_CORS_ORIGINS no puede ser '*' en producción")


validate_production(settings)
