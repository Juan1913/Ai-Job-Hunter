"""`python -m app.worker`: el trabajador 24/7 (búsquedas programadas, tareas lentas, avisos).

  python -m app.worker                 # bucle hasta SIGTERM
  python -m app.worker --once          # procesa lo pendiente y sale (útil para pruebas/cron)
  python -m app.worker --healthcheck   # exit 0 si ESTE host tiene un trabajador con latido reciente (healthcheck del contenedor)"""
from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.config import settings
from app.observability import configure_logging, metrics

log = logging.getLogger("jobhunter.worker")


def _wait_for_schema(timeout: float = 300.0) -> None:
    """La API (u Alembic) crea el esquema; el trabajador espera en vez de competir con ella al arrancar."""
    from sqlalchemy import text
    from sqlmodel import Session

    from app.db import engine
    deadline = time.monotonic() + timeout
    while True:
        try:
            with Session(engine) as s:
                s.execute(text("SELECT 1 FROM workitem LIMIT 1"))
            return
        except Exception as e:  # noqa: BLE001
            if time.monotonic() > deadline:
                raise SystemExit(f"el esquema no está listo tras {timeout:.0f} s: {e}")
            log.info("esperando al esquema de la BD...")
            time.sleep(3)


def _serve_metrics(port: int) -> None:
    from app.worker import health

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.startswith("/metrics"):
                body, code, ctype = metrics.render().encode(), 200, "text/plain; version=0.0.4; charset=utf-8"
            else:
                alive = bool(health.alive_workers(host=socket.gethostname()))
                body, code, ctype = (b'{"ok":true}' if alive else b'{"ok":false}'), (200 if alive else 503), "application/json"
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    try:
        srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    except OSError as e:                  # p. ej. dos trabajadores en el mismo host con el mismo puerto: las métricas son un extra, no un motivo para caer
        log.warning("no se pudo abrir el puerto de métricas %s: %s", port, e)
        return
    threading.Thread(target=srv.serve_forever, name="worker-metrics", daemon=True).start()
    log.info("métricas del trabajador en :%s/metrics", port)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.worker")
    ap.add_argument("--once", action="store_true", help="procesa lo pendiente y sale")
    ap.add_argument("--healthcheck", action="store_true", help="exit 0 si hay un trabajador vivo en este host")
    ap.add_argument("--concurrency", type=int, default=None)
    ap.add_argument("--id", default=None)
    a = ap.parse_args(argv)

    if a.healthcheck:
        from app.worker import health
        alive = health.alive_workers(host=socket.gethostname())
        print("ok" if alive else "sin latido reciente")
        return 0 if alive else 1

    configure_logging(settings.log_level, settings.log_json)
    from app.db import init_db
    if settings.db_auto_create:
        init_db()
    _wait_for_schema()
    from app.worker.loop import Worker
    w = Worker(a.id, concurrency=a.concurrency)
    if settings.worker_metrics_port:
        _serve_metrics(settings.worker_metrics_port)
    w.install_signals()
    if a.once:
        w.run_until_idle()
        w.shutdown()
        return 0
    w.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
