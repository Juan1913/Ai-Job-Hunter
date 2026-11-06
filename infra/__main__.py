"""Despliega JobHunter con Docker. El mismo programa sirve para local y VPS:
el destino lo define `docker:host` del stack (vacío = Docker local, `ssh://usuario@ip` = VPS por SSH).

Topología (red interna `jh-<stack>`; solo el proxy/API publican puertos):

    [Caddy :80/:443 con TLS automático]   <- solo si hay `domain` (stack vps)
         |-- /api/*, /health, /ready -> backend:8000
         '-- /* -> frontend:8080 (si hay imagen) o un mensaje
    backend (FastAPI, 1 proceso: solo atiende la API y ENCOLA) --> ollama:11434 (solo aiMode=local)
    worker (misma imagen, `python -m app.worker`: búsquedas programadas, tareas lentas, avisos 24/7)
         |--> sqlite en volumen /data compartido (mismo host)   o   postgres:5432 (database=postgres)
    ollama-pull (una vez: descarga modelos, solo aiMode=local) · backup (bucle: sqlite/pg_dump -> volumen/ruta del host)

Modo de IA (`jobhunter:aiMode`): local (Ollama en este host) | api (proveedor externo, sin Ollama) | none (sin IA: sin Ollama ni
descarga de modelos, para un VPS pequeño). Se propaga como JH_AI_MODE a backend y worker.

Decisiones (ver docs/adr/): API de un solo proceso + trabajador con cola en BD (0008), SQLite por defecto con respaldo
periódico (un host: API + 1 trabajador) y PostgreSQL para varios trabajadores, Caddy para TLS, modelos fijados por el despliegue.
"""
import json
from pathlib import Path

import pulumi
import pulumi_docker as docker

cfg = pulumi.Config()
stack = pulumi.get_stack()
work_dir = Path(__file__).resolve().parents[2]  # carpeta work/ (contexto de build)
here = Path(__file__).resolve().parent


# ------------------------------------------------------------------ configuración
def _bool(key: str, default: bool = False) -> bool:
    v = cfg.get_bool(key)
    return default if v is None else v


fast_model = cfg.get("modelFast") or "qwen2.5:3b-instruct"
writer_model = cfg.get("modelWriter") or "qwen2.5:7b-instruct"
embed_model = cfg.get("modelEmbed") or "nomic-embed-text"
models = list(dict.fromkeys([fast_model, writer_model, embed_model]))   # sin duplicados, en orden
ai_mode = (cfg.get("aiMode") or "local").lower()          # local | api | none
if ai_mode not in ("local", "api", "none"):
    raise ValueError("jobhunter:aiMode debe ser 'local', 'api' o 'none'")
uses_ollama = ai_mode == "local"                          # en api/none NO se levanta Ollama ni se descargan modelos

api_port = cfg.get_int("apiPort") or 8000
bind_host = cfg.get("bindHost") or "127.0.0.1"           # local: solo la propia máquina
domain = cfg.get("domain")                                # con dominio => Caddy + TLS automático
acme_email = cfg.get("acmeEmail")
database = (cfg.get("database") or "sqlite").lower()     # sqlite | postgres
if database not in ("sqlite", "postgres"):
    raise ValueError("jobhunter:database debe ser 'sqlite' o 'postgres'")
frontend_image = cfg.get("frontendImage")                 # ej. ghcr.io/org/jobhunter-web:1.0 (vacío = sin frontend)
frontend_placeholder = _bool("frontendPlaceholder")       # nginx de ejemplo hasta que exista la imagen real
frontend_port = cfg.get_int("frontendPort") or 3000
cors_origins = cfg.get("corsOrigins") or (f"https://{domain}" if domain else "")

# Recursos por contenedor (límites duros). Ollama es quien consume CPU/RAM.
ollama_cpus = cfg.get("ollamaCpus") or "6"
ollama_mem = cfg.get_int("ollamaMemoryMb") or 12288      # 7b q4 ~5 GB + contexto + margen
ollama_threads = cfg.get_int("ollamaThreads") or 6
backend_cpus = cfg.get("backendCpus") or "2"
backend_mem = cfg.get_int("backendMemoryMb") or 3072     # incluye Chromium para el modo stealth
pg_mem = cfg.get_int("postgresMemoryMb") or 1024
backup_every_h = cfg.get_int("backupIntervalHours") or 6
backup_keep = cfg.get_int("backupKeep") or 14
backup_host_path = cfg.get("backupHostPath")              # ruta del host para copias fuera del volumen
# Trabajador 24/7 (app/worker): misma imagen, otro comando. Con workerEnabled=false la API vuelve a ejecutar todo en sus hilos.
worker_enabled = _bool("workerEnabled", True)
worker_replicas = cfg.get_int("workerReplicas") or 1
worker_cpus = cfg.get("workerCpus") or "1.5"
worker_mem = cfg.get_int("workerMemoryMb") or 2048        # scraping con Chromium (modo stealth) + evaluación
worker_concurrency = cfg.get_int("workerConcurrency") or 2

# Secretos: `pulumi config set --secret jobhunter:jwtSecret "$(openssl rand -base64 48)"`
jwt_secret = cfg.get_secret("jwtSecret")
pg_password = cfg.get_secret("postgresPassword")
metrics_token = cfg.get_secret("metricsToken")
if domain and jwt_secret is None:
    raise ValueError("Con dominio (producción) hay que definir el secreto: pulumi config set --secret jobhunter:jwtSecret ...")
if database == "postgres" and pg_password is None:
    raise ValueError("database=postgres requiere: pulumi config set --secret jobhunter:postgresPassword ...")
if worker_enabled and worker_replicas > 1 and database != "postgres":
    # SQLite admite API + 1 trabajador en el mismo host (WAL, ver ADR 0008); con varios trabajadores el único escritor lo estrangula.
    raise ValueError("workerReplicas > 1 requiere jobhunter:database=postgres (SQLite: un solo escritor; ver docs/adr/0008)")
llm_api_key = cfg.get_secret("llmApiKey")                 # aiMode=api: clave del proveedor (JH_LLM_API_KEY); nunca en claro

is_prod = jwt_secret is not None            # sin secreto (solo local) el backend corre en modo dev
extra_env = cfg.get_object("extraEnv") or {}   # JSON {"JH_QUOTA_SEARCHES_PER_DAY": "10", ...}

name = f"jh-{stack}"
labels = [docker.ContainerLabelArgs(label="jobhunter.stack", value=stack)]


def net(*aliases: str):
    return [docker.ContainerNetworksAdvancedArgs(name=network.name, aliases=list(aliases))]


def healthcheck(*cmd: str, start_period: str = "20s", interval: str = "30s"):
    return docker.ContainerHealthcheckArgs(tests=["CMD", *cmd], interval=interval, timeout="5s", retries=3, start_period=start_period)


log_opts = {"max-size": "20m", "max-file": "5"}         # evita que los logs llenen el disco del VPS

# ------------------------------------------------------------------ red y volúmenes
network = docker.Network("jh-net", name=name)
models_volume = docker.Volume("jh-models", name=f"{name}-models") if uses_ollama else None
data_volume = docker.Volume("jh-data", name=f"{name}-data")
backups_volume = docker.Volume("jh-backups", name=f"{name}-backups")
pg_volume = docker.Volume("jh-pgdata", name=f"{name}-pgdata") if database == "postgres" else None


def backups_mount():
    if backup_host_path:
        return docker.ContainerVolumeArgs(host_path=backup_host_path, container_path="/backups")
    return docker.ContainerVolumeArgs(volume_name=backups_volume.name, container_path="/backups")


# ------------------------------------------------------------------ Ollama (+ descarga de modelos)
ollama = None
model_pull = None
if uses_ollama:
    ollama_image = docker.RemoteImage("ollama-image", name=cfg.get("ollamaImage") or "ollama/ollama:latest", keep_locally=True)

    ollama = docker.Container(
        "ollama",
        name=f"{name}-ollama",
        image=ollama_image.image_id,
        restart="unless-stopped",
        cpus=ollama_cpus,
        memory=ollama_mem,
        memory_swap=ollama_mem,          # sin swap: un modelo que se swapea tarda minutos por token
        init=True,
        envs=[
            f"OLLAMA_NUM_THREAD={ollama_threads}",
            "OLLAMA_NUM_PARALLEL=1",          # la CPU no paraleliza: una inferencia a la vez (la cola está en el backend)
            "OLLAMA_MAX_LOADED_MODELS=2",     # fast + writer residentes; el de embeddings es pequeño
            "OLLAMA_KEEP_ALIVE=30m",
            "OLLAMA_HOST=0.0.0.0:11434",
        ],
        volumes=[docker.ContainerVolumeArgs(volume_name=models_volume.name, container_path="/root/.ollama")],
        networks_advanced=net("ollama"),
        healthcheck=healthcheck("ollama", "list", start_period="30s"),
        log_opts=log_opts,
        labels=labels,
    )

    # Una sola vez (y de nuevo si cambian los modelos): espera a Ollama y descarga lo que falte.
    pull_script = (
        "until ollama list >/dev/null 2>&1; do sleep 2; done; "
        + "; ".join(f"ollama pull {m}" for m in models)
        + "; ollama list"
    )
    model_pull = docker.Container(
        "ollama-pull",
        name=f"{name}-ollama-pull",
        image=ollama_image.image_id,
        entrypoints=["/bin/sh", "-c"],
        command=[pull_script],
        envs=["OLLAMA_HOST=http://ollama:11434"],
        restart="on-failure",
        max_retry_count=5,
        must_run=False,                   # termina al acabar: no es un servicio
        cpus="1",
        memory=512,
        networks_advanced=net("ollama-pull"),
        log_opts=log_opts,
        labels=labels,
        opts=pulumi.ResourceOptions(depends_on=[ollama]),
    )

# ------------------------------------------------------------------ base de datos
postgres = None
if database == "postgres":
    postgres = docker.Container(
        "postgres",
        name=f"{name}-postgres",
        image=docker.RemoteImage("postgres-image", name=cfg.get("postgresImage") or "postgres:16-alpine", keep_locally=True).image_id,
        restart="unless-stopped",
        memory=pg_mem,
        envs=["POSTGRES_USER=jobhunter", "POSTGRES_DB=jobhunter", pulumi.Output.concat("POSTGRES_PASSWORD=", pg_password)],
        volumes=[docker.ContainerVolumeArgs(volume_name=pg_volume.name, container_path="/var/lib/postgresql/data")],
        networks_advanced=net("postgres"),
        healthcheck=healthcheck("pg_isready", "-U", "jobhunter", "-d", "jobhunter"),
        shm_size=128,
        log_opts=log_opts,
        labels=labels,
    )
    database_url = pulumi.Output.concat("postgresql+psycopg://jobhunter:", pg_password, "@postgres:5432/jobhunter")
else:
    database_url = "sqlite:////data/jobhunter.db"

# ------------------------------------------------------------------ backend
backend_image = docker.Image(
    "backend-image",
    image_name=f"jobhunter-backend:{stack}",
    build=docker.DockerBuildArgs(
        context=str(work_dir),
        dockerfile=str(work_dir / "jobhunter/backend/Dockerfile"),
        platform="linux/amd64",
    ),
    skip_push=True,
)

# Entorno común a la API y al trabajador (misma imagen, misma base de datos, mismo modo de IA).
common_envs: list = [
    f"JH_ENV={'prod' if is_prod else 'dev'}",
    f"JH_AI_MODE={ai_mode}",
    f"JH_LLM_MODEL_FAST={fast_model}",
    f"JH_LLM_MODEL_WRITER={writer_model}",
    f"JH_LLM_MODEL_EMBED={embed_model}",
    "JH_LLM_MAX_CONCURRENCY=1",
    pulumi.Output.concat("JH_DATABASE_URL=", database_url),
    "JH_DB_AUTO_CREATE=false",                   # el esquema lo crea Alembic en el entrypoint del backend
]
if uses_ollama:
    common_envs += ["JH_LLM_BASE_URL=http://ollama:11434/v1"]
else:
    # Sin Ollama: /ready no puede exigirlo y los embeddings caen al modo por hashing (sin red).
    common_envs += ["JH_READY_REQUIRES_LLM=false", "JH_LLM_WARMUP=false", "JH_EMBED_BACKEND=hash"]
if ai_mode == "api" and llm_api_key is not None:
    common_envs.append(pulumi.Output.concat("JH_LLM_API_KEY=", llm_api_key))
if is_prod:
    common_envs.append(pulumi.Output.concat("JH_JWT_SECRET=", jwt_secret))
common_envs += [f"{k}={v}" for k, v in sorted(extra_env.items())]

backend_envs: list = [
    *common_envs,
    f"JH_CORS_ORIGINS={cors_origins}",
    f"JH_TRUST_PROXY_HEADERS={'true' if domain else 'false'}",   # solo fiable detrás de Caddy
    f"JH_HSTS={'true' if domain else 'false'}",
]
if uses_ollama:
    backend_envs.append("JH_LLM_WARMUP=true")
if worker_enabled:
    backend_envs.append("JH_WORKER_EXTERNAL=true")          # la API encola; /ready exige un trabajador con latido reciente
if metrics_token is not None:
    backend_envs.append(pulumi.Output.concat("JH_METRICS_TOKEN=", metrics_token))

backend_deps = ([ollama] if ollama else []) + ([postgres] if postgres else [])
backend = docker.Container(
    "backend",
    name=f"{name}-backend",
    image=backend_image.image_name,
    restart="unless-stopped",
    cpus=backend_cpus,
    memory=backend_mem,
    memory_swap=backend_mem,
    shm_size=512,                                # Chromium se cae con el /dev/shm de 64 MB por defecto
    init=True,
    envs=backend_envs,
    volumes=[docker.ContainerVolumeArgs(volume_name=data_volume.name, container_path="/data")],
    # Con proxy (vps) la API no se publica; en local solo escucha en la propia máquina.
    ports=[] if domain else [docker.ContainerPortArgs(internal=8000, external=api_port, ip=bind_host)],
    networks_advanced=net("backend"),
    healthcheck=healthcheck(
        "python", "-c",
        "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)",
        start_period="60s"),
    log_opts=log_opts,
    labels=labels,
    opts=pulumi.ResourceOptions(depends_on=backend_deps),
)

# ------------------------------------------------------------------ trabajador 24/7 (búsquedas programadas, tareas lentas, avisos)
# Misma imagen y mismo volumen/BD que la API. NO migra (lo hace el backend): espera a que exista el esquema.
# Healthcheck = latido en BD de ESTE contenedor (si el bucle se cuelga, Docker lo marca unhealthy y `restart` lo relanza).
workers: list = []
if worker_enabled:
    worker_envs = [*common_envs, "JH_WORKER_EXTERNAL=true", "JH_RUN_MIGRATIONS=false", f"JH_WORKER_CONCURRENCY={worker_concurrency}",
                   "JH_WORKER_METRICS_PORT=9108", "JH_LOG_JSON=true"]
    for i in range(worker_replicas):
        suffix = "" if worker_replicas == 1 else f"-{i + 1}"
        workers.append(docker.Container(
            f"worker{suffix}",
            name=f"{name}-worker{suffix}",
            image=backend_image.image_name,
            command=["python", "-m", "app.worker"],
            restart="unless-stopped",
            cpus=worker_cpus,
            memory=worker_mem,
            memory_swap=worker_mem,
            shm_size=512,
            init=True,
            envs=worker_envs,
            volumes=[docker.ContainerVolumeArgs(volume_name=data_volume.name, container_path="/data")],
            networks_advanced=net(f"worker{suffix}"),
            healthcheck=healthcheck("python", "-m", "app.worker", "--healthcheck", start_period="90s", interval="30s"),
            log_opts=log_opts,
            labels=labels,
            opts=pulumi.ResourceOptions(depends_on=[backend] + ([postgres] if postgres else [])),
        ))

# ------------------------------------------------------------------ respaldo periódico
backup_envs = [f"INTERVAL_HOURS={backup_every_h}", f"KEEP={backup_keep}", "BACKUP_DIR=/backups"]
if database == "sqlite":
    backup = docker.Container(
        "backup",
        name=f"{name}-backup",
        image=backend_image.image_name,
        entrypoints=["python", "/backup/sqlite_backup.py"],
        restart="unless-stopped",
        memory=128,
        user="10001",
        envs=backup_envs + ["DB_PATH=/data/jobhunter.db"],
        volumes=[docker.ContainerVolumeArgs(volume_name=data_volume.name, container_path="/data"), backups_mount()],
        uploads=[docker.ContainerUploadArgs(file="/backup/sqlite_backup.py", content=(here / "backup/sqlite_backup.py").read_text(), permissions="0644")],
        networks_advanced=net("backup"),
        log_opts=log_opts,
        labels=labels,
        opts=pulumi.ResourceOptions(depends_on=[backend]),
    )
else:
    backup = docker.Container(
        "backup",
        name=f"{name}-backup",
        image=postgres.image,
        entrypoints=["/bin/sh", "/backup/pg_backup.sh"],
        restart="unless-stopped",
        memory=256,
        envs=backup_envs + ["PGHOST=postgres", "PGUSER=jobhunter", "PGDATABASE=jobhunter", pulumi.Output.concat("PGPASSWORD=", pg_password)],
        volumes=[backups_mount()],
        uploads=[docker.ContainerUploadArgs(file="/backup/pg_backup.sh", content=(here / "backup/pg_backup.sh").read_text(), permissions="0755")],
        networks_advanced=net("backup"),
        log_opts=log_opts,
        labels=labels,
        opts=pulumi.ResourceOptions(depends_on=[postgres]),
    )

# ------------------------------------------------------------------ frontend (opcional, listo para activarse)
frontend = None
fe_image_name = frontend_image or ("nginxinc/nginx-unprivileged:1.27-alpine" if frontend_placeholder else None)
if fe_image_name:
    fe_img = docker.RemoteImage("frontend-image", name=fe_image_name, keep_locally=True)
    frontend = docker.Container(
        "frontend",
        name=f"{name}-frontend",
        image=fe_img.image_id,
        restart="unless-stopped",
        memory=128,
        read_only=False,
        # nginx-unprivileged escucha en 8080; en local se publica en frontendPort, en vps lo ve solo Caddy.
        ports=[] if domain else [docker.ContainerPortArgs(internal=8080, external=frontend_port, ip=bind_host)],
        networks_advanced=net("frontend"),
        healthcheck=healthcheck("wget", "-qO-", "http://127.0.0.1:8080/"),
        log_opts=log_opts,
        labels=labels,
    )

# ------------------------------------------------------------------ proxy inverso con TLS automático (solo con dominio)
proxy = None
if domain:
    fallback = "reverse_proxy frontend:8080" if frontend else 'respond "JobHunter API - el frontend aún no está desplegado" 200'
    caddyfile = f"""{{
    {f'email {acme_email}' if acme_email else '# sin email ACME: Let\'s Encrypt funciona igual pero sin avisos de caducidad'}
}}

{domain} {{
    encode zstd gzip
    request_body {{
        max_size 10MB
    }}
    # Las métricas y la documentación no se exponen a Internet.
    @private path /metrics /docs /redoc /openapi.json
    respond @private 404

    @api path /api/* /health /ready
    handle @api {{
        reverse_proxy backend:8000 {{
            # Las búsquedas y el LLM son lentos: no cortar peticiones largas.
            transport http {{
                response_header_timeout 300s
            }}
        }}
    }}
    handle {{
        {fallback}
    }}
    header {{
        -Server
    }}
}}
"""
    proxy = docker.Container(
        "proxy",
        name=f"{name}-proxy",
        image=docker.RemoteImage("caddy-image", name=cfg.get("caddyImage") or "caddy:2-alpine", keep_locally=True).image_id,
        restart="unless-stopped",
        memory=256,
        ports=[
            docker.ContainerPortArgs(internal=80, external=80),
            docker.ContainerPortArgs(internal=443, external=443),
            docker.ContainerPortArgs(internal=443, external=443, protocol="udp"),   # HTTP/3
        ],
        volumes=[
            docker.ContainerVolumeArgs(volume_name=docker.Volume("caddy-data", name=f"{name}-caddy-data").name, container_path="/data"),
            docker.ContainerVolumeArgs(volume_name=docker.Volume("caddy-config", name=f"{name}-caddy-config").name, container_path="/config"),
        ],
        uploads=[docker.ContainerUploadArgs(file="/etc/caddy/Caddyfile", content=caddyfile)],
        networks_advanced=net("proxy"),
        log_opts=log_opts,
        labels=labels,
        opts=pulumi.ResourceOptions(depends_on=[backend] + ([frontend] if frontend else [])),
    )

# ------------------------------------------------------------------ outputs
public_url = f"https://{domain}" if domain else f"http://localhost:{api_port}"
pulumi.export("api_url", public_url)
pulumi.export("health_url", f"{public_url}/health")
pulumi.export("ready_url", f"{public_url}/ready")
pulumi.export("frontend_url", (f"https://{domain}" if domain else f"http://localhost:{frontend_port}") if frontend else "no desplegado (define jobhunter:frontendImage)")
pulumi.export("models", models if uses_ollama else [])
pulumi.export("ai_mode", ai_mode)
pulumi.export("database", database)
pulumi.export("containers", {
    "backend": backend.name, "backup": backup.name,
    **({"ollama": ollama.name, "model_pull": model_pull.name} if ollama else {}),
    **({f"worker{i + 1}": w.name for i, w in enumerate(workers)} if workers else {}),
    **({"postgres": postgres.name} if postgres else {}), **({"proxy": proxy.name} if proxy else {}),
    **({"frontend": frontend.name} if frontend else {}),
})
pulumi.export("volumes", {"data": data_volume.name, "backups": backups_volume.name,
                          **({"models": models_volume.name} if models_volume else {}), **({"pgdata": pg_volume.name} if pg_volume else {})})
pulumi.export("backups_location", backup_host_path or f"volumen docker {name}-backups (cada {backup_every_h} h, se conservan {backup_keep})")
pulumi.export("logs_hint", f"docker logs -f {name}-backend  |  docker logs -f {name}-worker" + (f"  |  docker logs -f {name}-ollama-pull  (progreso de la descarga de modelos)" if uses_ollama else ""))
pulumi.export("effective_config", json.dumps({"env": "prod" if is_prod else "dev", "tls": bool(domain), "database": database, "aiMode": ai_mode,
                                              "worker": {"enabled": worker_enabled, "replicas": worker_replicas if worker_enabled else 0},
                                              "frontend": bool(frontend), "extraEnv": sorted(extra_env)}))
