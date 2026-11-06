# syntax=docker/dockerfile:1.7
# Contexto de build: la carpeta work/ (contiene Scrapling-main/ y jobhunter/):
#   docker build -f jobhunter/backend/Dockerfile -t jobhunter-backend work/
ARG PYTHON_IMAGE=python:3.13-slim

# ---------------------------------------------------------------- builder: dependencias en un venv
FROM ${PYTHON_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /src

# Scrapling local (fuente de la carpeta hermana) con sus fetchers
COPY Scrapling-main /src/scrapling
RUN pip install "/src/scrapling[fetchers]"

# Dependencias del backend leídas de pyproject.toml (capa cacheable: solo cambia si cambian las deps)
COPY jobhunter/backend/pyproject.toml /src/backend/pyproject.toml
RUN python - <<'PY' > /tmp/requirements.txt \
 && pip install -r /tmp/requirements.txt
import tomllib
p = tomllib.load(open("/src/backend/pyproject.toml", "rb"))["project"]
print("\n".join(p["dependencies"] + p["optional-dependencies"].get("postgres", [])))
PY

# ---------------------------------------------------------------- runtime
FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PATH=/opt/venv/bin:$PATH \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    JH_ENV=prod JH_DB_AUTO_CREATE=false \
    JH_DATABASE_URL=sqlite:////data/jobhunter.db

COPY --from=builder /opt/venv /opt/venv

# Chromium para el modo stealth de Scrapling (patchright) y el dinámico (playwright), con sus
# librerías del sistema. Se instala como root en una ruta legible por el usuario sin privilegios.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini \
 && playwright install --with-deps chromium \
 && patchright install chromium \
 && rm -rf /var/lib/apt/lists/* /root/.cache \
 && chmod -R a+rX /opt/ms-playwright

RUN useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin jobhunter \
 && mkdir -p /data /backups && chown jobhunter:jobhunter /data /backups

WORKDIR /app
COPY --chown=jobhunter:jobhunter jobhunter/backend/app /app/app
COPY --chown=jobhunter:jobhunter jobhunter/backend/migrations /app/migrations
COPY --chown=jobhunter:jobhunter jobhunter/backend/alembic.ini /app/alembic.ini
COPY --chown=jobhunter:jobhunter jobhunter/backend/docker-entrypoint.sh /app/docker-entrypoint.sh
ENV PYTHONPATH=/app

USER jobhunter
VOLUME ["/data", "/backups"]
EXPOSE 8000

# Liveness barata (no toca BD ni LLM); la readiness completa está en /ready.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker-entrypoint.sh"]
# Un solo proceso: rate limits, cola del LLM y pool de búsquedas viven en memoria.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log"]
