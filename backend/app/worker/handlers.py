"""Qué hace el trabajador con cada tipo de trabajo de la cola. Cada tipo tiene `run` (ejecuta) y `dead` (qué anotar si va a dead-letter)."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlmodel import Session

from app.db import JobTask, SearchRun, engine, utcnow
from app.worker.queue import Claimed

log = logging.getLogger(__name__)


@dataclass
class Kind:
    run: Callable[[Claimed], None]
    dead: Callable[[Claimed, str], None]


# ------------------------------------------------------------------ búsquedas (manuales y programadas)
def _search_run(item: Claimed) -> None:
    from app.scheduling import automation
    from app.services import runner
    with Session(engine) as s:
        run = s.get(SearchRun, item.ref_id)
        status = run.status if run else None
    if status is None:
        return                                           # la cuenta se borró entre medias
    if status in ("done", "failed"):
        automation.after_run(item.ref_id)                # ya terminó (caída justo después): solo faltaba lo posterior, que es idempotente
        return
    runner.execute_run(item.ref_id)                      # no lanza: marca la búsqueda `failed` si algo falla y llama a la automatización


def _search_dead(item: Claimed, error: str) -> None:
    from app.scheduling import automation
    with Session(engine) as s:
        run = s.get(SearchRun, item.ref_id)
        if run is None or run.status in ("done", "failed"):
            return
        run.status, run.error, run.finished_at = "failed", f"abandonada tras {item.attempts} intentos: {error}"[:500], utcnow()
        s.add(run)
        s.commit()
    automation.after_run(item.ref_id)                    # cuenta el fallo en la búsqueda programada (backoff / pausa)


# ------------------------------------------------------------------ tareas lentas (analyze, tailor, prep, evaluate, ...)
def _task_run(item: Claimed) -> None:
    from app.llm.factory import build_llm
    from app.services.tasks import execute_task
    with Session(engine) as s:
        t = s.get(JobTask, item.ref_id)
        if t is None or t.status in ("done", "failed"):
            return
        uid = t.user_id
    # Clientes POR USUARIO: en modo api respetan su consentimiento y presupuesto; sin IA llegan deshabilitados y el handler degrada.
    execute_task(item.ref_id, build_llm("fast", uid), build_llm("writer", uid))


def _task_dead(item: Claimed, error: str) -> None:
    with Session(engine) as s:
        t = s.get(JobTask, item.ref_id)
        if t is None or t.status in ("done", "failed"):
            return
        t.status, t.error, t.finished_at = "failed", f"abandonada tras {item.attempts} intentos: {error}"[:500], utcnow()
        s.add(t)
        s.commit()


# ------------------------------------------------------------------ entregas de avisos
def _deliver_run(item: Claimed) -> None:
    from app.notifications import service
    service.deliver(item.ref_id)


def _deliver_dead(item: Claimed, error: str) -> None:
    from app.notifications import service
    service.mark_dead(item.ref_id, error)


KINDS: dict[str, Kind] = {
    "search": Kind(_search_run, _search_dead),
    "task": Kind(_task_run, _task_dead),
    "deliver": Kind(_deliver_run, _deliver_dead),
}
