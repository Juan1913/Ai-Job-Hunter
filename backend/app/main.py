import logging
import threading
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from starlette.middleware.cors import CORSMiddleware

from app import middleware as mw
from app.api import account, ai, auth, companies, health, jobs, profile, searches, tasks
from app.api.deps import enforce_quotas
from app.autoapply import api as autoapply_api
from app.config import settings
from app.db import init_db
from app.geo import api as geo_api
from app.llm import client as llm_client
from app.notifications import api as notifications_api
from app.observability import configure_logging
from app.prep import api as prep_api
from app.scheduling import api as scheduling_api
from app.security import login_guard
from app.tracking import api as tracking_api

log = logging.getLogger("jobhunter")


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging(settings.log_level, settings.log_json)
    mw.reset_state()                      # límites y bloqueos en memoria arrancan limpios
    login_guard.reset()
    if settings.db_auto_create:           # en producción el esquema lo gestiona Alembic
        init_db()
    if settings.llm_warmup:               # carga los modelos en RAM sin retrasar el arranque
        threading.Thread(target=llm_client.warmup_models, name="llm-warmup", daemon=True).start()
    log.info("JobHunter listo", extra={"env": settings.env})
    yield
    llm_client.close_shared()


app = FastAPI(title="JobHunter", version="0.3.0", lifespan=lifespan,
              docs_url=None if settings.env == "prod" else "/docs",
              redoc_url=None, openapi_url=None if settings.env == "prod" else "/openapi.json")

# Orden: lo último añadido es lo más externo. CORS va fuera para que hasta los 429 lleven sus cabeceras.
app.add_middleware(mw.AppMiddleware)
_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
if _origins:
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
                       expose_headers=["X-Request-ID", "Retry-After"], max_age=600)

app.include_router(health.router)
for r in (ai.router, auth.router, account.router, profile.router, companies.router):
    app.include_router(r, prefix="/api/v1")
# Las rutas que gastan CPU de LLM / red llevan cuota diaria por usuario.
for r in (jobs.router, searches.router, tasks.router):
    app.include_router(r, prefix="/api/v1", dependencies=[Depends(enforce_quotas)])
# Seguimiento de candidaturas y preparación: no consumen cuota de LLM por petición (las tareas lentas van en segundo plano).
# Búsquedas programadas y avisos: solo gestionan filas; las ejecuta el trabajador (python -m app.worker).
for r in (autoapply_api.router, geo_api.router, tracking_api.router, prep_api.router, scheduling_api.router, notifications_api.router):
    app.include_router(r, prefix="/api/v1")
