from datetime import datetime, timezone

from sqlalchemy import JSON, Column, LargeBinary, UniqueConstraint, event
from sqlmodel import Field, Session, SQLModel, create_engine

from app.config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


_is_sqlite = settings.database_url.startswith("sqlite")
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False, "timeout": 30} if _is_sqlite else {},   # timeout: espera de bloqueo con API y trabajador a la vez
    pool_pre_ping=not _is_sqlite,      # PostgreSQL: detecta conexiones muertas tras reinicios de la BD
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _):
    if settings.database_url.startswith("sqlite"):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA synchronous=NORMAL")      # seguro con WAL: solo la última transacción puede perderse ante un corte de luz
        cur.close()


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    password_hash: str
    created_at: datetime = Field(default_factory=utcnow)


class ProfileRow(SQLModel, table=True):
    user_id: int = Field(primary_key=True, foreign_key="user.id")
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=utcnow)


class SearchRun(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    status: str = "queued"                 # queued | running | done | failed
    params: dict = Field(default_factory=dict, sa_column=Column(JSON))
    sources: dict = Field(default_factory=dict, sa_column=Column(JSON))   # fuente -> {status, found, error}
    new_jobs: int = 0
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


class JobRow(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("user_id", "dedup_key"),)
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    dedup_key: str = Field(index=True)
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))       # Job
    status: str = Field(default="new", index=True)
    stage1: float | None = None            # puntaje barato 0-100 (sin LLM)
    match: int | None = Field(default=None, index=True)   # puntaje tras análisis LLM
    gaps: list | None = Field(default=None, sa_column=Column(JSON))
    evaluation: dict | None = Field(default=None, sa_column=Column(JSON))   # informe de app/evaluation (puntaje 1-5, vetos, dimensiones)
    run_id: int | None = Field(default=None, foreign_key="searchrun.id")
    first_seen: datetime = Field(default_factory=utcnow)
    last_seen: datetime = Field(default_factory=utcnow)


class TailoredRow(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    job_id: int = Field(foreign_key="jobrow.id", index=True)
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))       # TailoredCV
    confirmed: list = Field(default_factory=list, sa_column=Column(JSON))
    ats_score: int = 0
    created_at: datetime = Field(default_factory=utcnow)


class JobEvent(SQLModel, table=True):
    """Línea de tiempo auditable: qué pasó con cada oferta y cuándo."""
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    job_id: int = Field(foreign_key="jobrow.id", index=True)
    kind: str                              # discovered|analyzed|tailored|apply_opened|status:<x>
    note: str | None = None
    ts: datetime = Field(default_factory=utcnow)


class JobTask(SQLModel, table=True):
    """Operaciones lentas con LLM (analizar, adaptar CV, carta) que corren en segundo plano."""
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    job_id: int = Field(foreign_key="jobrow.id", index=True)
    kind: str                              # analyze | tailor | cover_letter
    params: dict = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = "queued"                 # queued | running | done | failed
    result: dict | None = Field(default=None, sa_column=Column(JSON))
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


class Company(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int | None = Field(default=None, foreign_key="user.id", index=True)  # null = global
    name: str
    kind: str                              # greenhouse | lever | careers | ...
    token: str | None = None
    url: str | None = None
    stealth: bool = False
    active: bool = True
    verified_at: datetime | None = None


class ProfileNode(SQLModel, table=True):
    """Árbol jerárquico del perfil (RAG estilo RAPTOR): hojas -> experiencias/áreas -> raíz.
    Se reconstruye cuando cambia el contenido del perfil (profile_hash) o el modelo de embeddings."""
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    level: int = 0                         # 0 hoja | 1 intermedio | 2 raíz
    kind: str = ""                         # bullet|skill|cert|summary|experience|skills|certs|root
    parent_id: int | None = Field(default=None, index=True)
    ref: str = ""                          # ruta estable en el perfil, p. ej. experience:0:bullet:1
    text: str = ""                         # hojas: texto LITERAL del perfil (evidencia citable)
    embedding: bytes = Field(default=b"", sa_column=Column(LargeBinary))   # float32 normalizado
    embed_model: str = ""
    profile_hash: str = Field(default="", index=True)


class EmbeddingCache(SQLModel, table=True):
    """Caché de embeddings por hash(modelo + texto): evita recalcular ofertas y perfiles repetidos."""
    key: str = Field(primary_key=True)
    model: str = ""
    vector: bytes = Field(default=b"", sa_column=Column(LargeBinary))
    created_at: datetime = Field(default_factory=utcnow)


class SourceHealth(SQLModel, table=True):
    """Salud por fuente de empleo: la alimentan las búsquedas reales y el canario (scripts/verify_sources.py)."""
    source: str = Field(primary_key=True)
    status: str = "unknown"                # ok | degraded | error | unknown
    last_found: int = 0
    last_error: str | None = None
    note: str | None = None
    consecutive_zero: int = 0              # búsquedas seguidas con 0 resultados y sin error (fallo silencioso)
    consecutive_errors: int = 0
    runs: int = 0
    last_latency_ms: int | None = None
    last_run_at: datetime | None = None
    last_ok_at: datetime | None = None
    last_canary_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utcnow)


class ApplicationRow(SQLModel, table=True):
    """Pipeline real de una oferta (más allá de JobRow.status): fechas, entrevistas, contactos, resultado y el CV/carta usados.
    El sistema NUNCA envía solicitudes: el usuario aplica por su cuenta y aquí lo registra."""
    __table_args__ = (UniqueConstraint("user_id", "job_id"),)
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    job_id: int = Field(foreign_key="jobrow.id", index=True)
    stage: str = Field(default="saved", index=True)        # saved | applied | screening | interviewing | offer | closed
    tailored_id: int | None = Field(default=None, foreign_key="tailoredrow.id")   # CV exacto usado
    template: str | None = None                            # plantilla del CV enviado
    cover_letter: str | None = None                        # texto exacto de la carta enviada
    applied_at: datetime | None = None
    next_step: str | None = None
    next_step_at: datetime | None = None
    deadline_at: datetime | None = None                    # vence la oferta / plazo para aplicar
    first_response_at: datetime | None = None
    last_contact_at: datetime | None = None
    outcome: str | None = Field(default=None, index=True)  # offer | rejected | no_response | withdrawn
    outcome_reason: str | None = None
    outcome_at: datetime | None = None
    interviews: list = Field(default_factory=list, sa_column=Column(JSON))   # [{id, kind, scheduled_at, done, interviewer, notes, result}]
    contacts: list = Field(default_factory=list, sa_column=Column(JSON))     # [{id, name, role, email, phone, notes}]
    notes: str | None = None
    expected_salary_min: float | None = None
    expected_salary_max: float | None = None
    expected_salary_currency: str | None = None
    snoozed_until: datetime | None = None                  # silencia los recordatorios de esta oferta
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ApplicationArchive(SQLModel, table=True):
    """Archivo INMUTABLE de lo enviado: la oferta puede desaparecer o cambiar, esto no. Solo se añade, nunca se edita."""
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    job_id: int = Field(foreign_key="jobrow.id", index=True)
    application_id: int = Field(foreign_key="applicationrow.id", index=True)
    job_snapshot: dict = Field(default_factory=dict, sa_column=Column(JSON))        # Job tal como era al aplicar
    cv_snapshot: dict | None = Field(default=None, sa_column=Column(JSON))          # TailoredCV exacto
    cv_text: str = ""
    cover_letter: str | None = None
    template: str | None = None
    match: int | None = None
    content_hash: str = Field(default="", index=True)
    created_at: datetime = Field(default_factory=utcnow)


@event.listens_for(ApplicationArchive, "before_update")
def _archive_no_update(*_):
    raise ValueError("el archivo de lo enviado es inmutable")


@event.listens_for(ApplicationArchive, "before_delete")
def _archive_no_delete(*_):
    raise ValueError("el archivo de lo enviado es inmutable")


class PrepRow(SQLModel, table=True):
    """Resultado cacheado de la preparación (historias STAR, preguntas, negociación, plan de mejora) por oferta."""
    __table_args__ = (UniqueConstraint("user_id", "job_id", "kind"),)
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    job_id: int = Field(foreign_key="jobrow.id", index=True)
    kind: str                                              # stories | questions | ask | negotiation | upskill
    lang: str = "es"
    input_hash: str = Field(default="", index=True)        # huella del perfil + oferta + brechas: si no cambia, se reutiliza
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)


class QueryExpansion(SQLModel, table=True):
    """Caché de la expansión de consulta por LLM (app/scout/query.py): clave = hash(consulta normalizada + idiomas + versión del diccionario)."""
    key: str = Field(primary_key=True)
    keywords: str = ""
    variants: list = Field(default_factory=list, sa_column=Column(JSON))
    model: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class WorkItem(SQLModel, table=True):
    """Cola persistente del trabajador (app/worker): un trabajo con lease (`locked_by`/`locked_until`), reintentos y dead-letter."""
    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(index=True)                          # search | task | deliver
    ref_id: int | None = None                              # SearchRun.id | JobTask.id | NotificationDelivery.id
    user_id: int | None = Field(default=None, foreign_key="user.id", index=True)
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    state: str = Field(default="queued", index=True)       # queued | running | done | dead
    priority: int = 0                                      # mayor = antes (lo pedido por el usuario pasa por delante de lo programado)
    attempts: int = 0
    max_attempts: int = 3
    run_after: datetime = Field(default_factory=utcnow, index=True)
    locked_by: str | None = None
    locked_until: datetime | None = None
    last_error: str | None = None
    dedup_key: str | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class WorkerHeartbeat(SQLModel, table=True):
    """Latido de cada trabajador: /ready y el healthcheck del contenedor lo leen."""
    worker_id: str = Field(primary_key=True)
    host: str = Field(default="", index=True)
    pid: int = 0
    state: str = "running"                 # running | draining | stopped
    running: int = 0                       # trabajos en curso
    concurrency: int = 0
    started_at: datetime = Field(default_factory=utcnow)
    beat_at: datetime = Field(default_factory=utcnow, index=True)
    stats: dict = Field(default_factory=dict, sa_column=Column(JSON))


class SavedSearch(SQLModel, table=True):
    """Búsqueda programada de un usuario: el trabajador la lanza cada `interval_minutes` (con jitter, ventana horaria y backoff)."""
    __table_args__ = (UniqueConstraint("user_id", "name"),)
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    name: str
    params: dict = Field(default_factory=dict, sa_column=Column(JSON))     # keywords, limit_per_source, countries, remote, hours_old...
    interval_minutes: int = 360
    timezone: str = "UTC"
    window_start: str | None = None        # "HH:MM" en `timezone`; con window_end define la ventana horaria permitida
    window_end: str | None = None
    window_days: list = Field(default_factory=list, sa_column=Column(JSON))   # 0=lunes..6=domingo; vacío = todos
    active: bool = True
    paused_reason: str | None = None
    next_run_at: datetime = Field(default_factory=utcnow, index=True)
    last_run_at: datetime | None = None
    last_run_id: int | None = None
    failure_streak: int = 0
    total_runs: int = 0
    last_error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class SourceCache(SQLModel, table=True):
    """Caché COMPARTIDA entre usuarios de resultados crudos de una fuente: clave = (versión, fuente, país, hash de la consulta normalizada).
    Solo guarda lo que devolvió el portal; la evaluación y el puntaje se calculan siempre por usuario."""
    key: str = Field(primary_key=True)
    source: str = Field(index=True)
    country: str = ""
    query_text: str = ""                   # consulta normalizada legible (para inspección)
    payload: list = Field(default_factory=list, sa_column=Column(JSON))    # [Job.model_dump()]
    n: int = 0
    hits: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime = Field(default_factory=utcnow, index=True)


class RequestBudget(SQLModel, table=True):
    """Contador de peticiones salientes a portales por hora y ámbito ('global' o 'domain:<host>')."""
    bucket: str = Field(primary_key=True)  # "2026-11-04T13|global"
    hour: str = Field(index=True)
    scope: str = ""
    count: int = 0


class NotificationPref(SQLModel, table=True):
    """Política de automatización y de avisos de un usuario (todo opcional; los valores por defecto no molestan)."""
    user_id: int = Field(primary_key=True, foreign_key="user.id")
    auto_evaluate: bool = True             # evaluar solas las ofertas nuevas tras cada búsqueda (determinista, sin IA)
    apply_to_manual: bool = False          # aplicar la política también a las búsquedas lanzadas a mano
    auto_tailor: bool = False              # pre-generar el CV adaptado SOLO para ofertas 'apply'
    tailor_daily_budget: int = 3
    min_score: float = 3.0                 # puntaje (1-5) mínimo para avisar
    timezone: str = "UTC"
    quiet_start: str | None = None         # "HH:MM": silencio nocturno (solo retrasa los canales de salida; el aviso en-app existe igual)
    quiet_end: str | None = None
    digest: str = "off"                    # off | daily | weekly
    digest_hour: int = 8
    digest_weekday: int = 0                # 0=lunes (solo weekly)
    last_digest_key: str | None = None
    updated_at: datetime = Field(default_factory=utcnow)


class Notification(SQLModel, table=True):
    """Aviso en-app. `dedup_key` único por usuario: el mismo hecho nunca se avisa dos veces."""
    __table_args__ = (UniqueConstraint("user_id", "dedup_key"),)
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    kind: str = Field(index=True)          # new_jobs | digest | search_paused | channel_disabled | test | system
    title: str = ""
    body: str = ""
    job_id: int | None = Field(default=None, foreign_key="jobrow.id")
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))
    dedup_key: str | None = None
    read_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow, index=True)


class PendingAlert(SQLModel, table=True):
    """Oferta que califica para avisar y espera al resumen (digest) del usuario."""
    __table_args__ = (UniqueConstraint("user_id", "job_id"),)
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    job_id: int = Field(foreign_key="jobrow.id", index=True)
    score: float = 0.0
    created_at: datetime = Field(default_factory=utcnow)


class NotificationChannel(SQLModel, table=True):
    """Canal de salida opcional de un usuario. `secret_enc` (token/clave HMAC) va cifrado y NUNCA sale en las respuestas."""
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    kind: str                              # webhook | ntfy | telegram | email
    label: str = ""
    config: dict = Field(default_factory=dict, sa_column=Column(JSON))     # url / topic / chat_id (sin secretos)
    secret_enc: str | None = None
    enabled: bool = True
    failures: int = 0                      # entregas muertas seguidas
    last_ok_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class NotificationDelivery(SQLModel, table=True):
    """Registro de entrega de un aviso por un canal."""
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    notification_id: int = Field(foreign_key="notification.id", index=True)
    channel_id: int = Field(foreign_key="notificationchannel.id", index=True)
    status: str = "pending"                # pending | sent | dead
    attempts: int = 0
    response_code: int | None = None
    last_error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    delivered_at: datetime | None = None


class ApplyDataRow(SQLModel, table=True):
    """Datos del usuario para rellenar formularios de postulación (teléfono, enlaces, respuestas frecuentes). Solo se usan para sus candidaturas."""
    user_id: int = Field(primary_key=True, foreign_key="user.id")
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=utcnow)


class AutoApplyPolicyRow(SQLModel, table=True):
    """Política de auto-postulación del usuario. Por defecto TODO apagado: sin esta fila el sistema no envía nada."""
    user_id: int = Field(primary_key=True, foreign_key="user.id")
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=utcnow)


class ApplyAttempt(SQLModel, table=True):
    """Intento de postulación asistida/automática: auditable (qué CV, qué portal, qué pasó) e idempotente por oferta."""
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    job_id: int = Field(foreign_key="jobrow.id", index=True)
    status: str = Field(default="queued", index=True)    # awaiting_approval | queued | running | submitted | dry_run | needs_manual | unconfirmed | failed | rejected
    trigger: str = "manual"                              # auto | manual | approved
    adapter: str = ""
    cv_mode: str = "tailored"                            # base | tailored
    tailored_id: int | None = Field(default=None, foreign_key="tailoredrow.id")
    dry_run: bool = True
    detail: dict = Field(default_factory=dict, sa_column=Column(JSON))
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as s:
        yield s


import app.ai.models  # noqa: E402,F401  (registra LLMUsage y AiConsent en el metadata)
