"""Ejecuta en segundo plano las operaciones lentas con LLM. En CPU un análisis tarda ~1-2 min y un CV ~3 min:
no caben en una petición HTTP. El panel lanza la tarea y consulta su estado.

Los tipos de tarea se registran con `@register("nombre")` en app/services/handlers/*.py; así cada módulo añade el suyo
sin tocar este archivo."""
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from fastapi import HTTPException
from sqlmodel import Session, col, select

from app.config import settings
from app.db import JobTask, User, engine, utcnow

log = logging.getLogger(__name__)
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="task")   # el LLM serializa las inferencias (FairGate)


@dataclass
class TaskContext:
    session: Session
    user: User
    job_id: int
    params: dict
    llm: object        # modelo ligero (extracción, clasificación)
    writer: object     # modelo de redacción


Handler = Callable[[TaskContext], dict]
HANDLERS: dict[str, Handler] = {}
QUOTA_KIND: dict[str, str] = {}      # tipo de tarea -> cuota diaria que consume ('tailor' | 'analyze')


def register(kind: str, quota: str | None = None) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        if quota:
            QUOTA_KIND[kind] = quota
        return fn
    return deco


def kinds() -> tuple[str, ...]:
    from app.services import handlers  # noqa: F401  (importa los módulos que registran tareas)
    return tuple(HANDLERS)


def start_task(session: Session, user: User, job_id: int, kind: str, params: dict, llm, writer) -> JobTask:
    active = session.exec(select(JobTask).where(JobTask.user_id == user.id, JobTask.job_id == job_id, JobTask.kind == kind,
                                                col(JobTask.status).in_(["queued", "running"]))).first()
    if active:
        return active                       # idempotente: doble clic en el panel no duplica trabajo
    task = JobTask(user_id=user.id, job_id=job_id, kind=kind, params=params)
    session.add(task)
    session.commit()
    session.refresh(task)
    if settings.run_inline:
        execute_task(task.id, llm, writer)
    elif settings.worker_external:
        from app.worker import queue  # el trabajador crea sus propios clientes LLM; la API solo encola
        queue.enqueue(session, "task", ref_id=task.id, user_id=user.id, payload={"task_id": task.id}, priority=10, dedup_key=f"task:{task.id}")
    else:
        _pool.submit(execute_task, task.id, llm, writer)
    return task


def _finish(task_id: int, **fields) -> None:
    with Session(engine) as s:
        t = s.get(JobTask, task_id)
        for k, v in fields.items():
            setattr(t, k, v)
        s.add(t)
        s.commit()


def execute_task(task_id: int, llm, writer) -> None:
    kinds()
    try:
        with Session(engine) as s:
            t = s.get(JobTask, task_id)
            handler = HANDLERS.get(t.kind)
            if handler is None:
                raise ValueError(f"tarea desconocida: {t.kind}")
            ctx = TaskContext(s, s.get(User, t.user_id), t.job_id, dict(t.params), llm, writer)
            t.status = "running"
            s.add(t)
            s.commit()
            result = handler(ctx)
        _finish(task_id, status="done", result=result, finished_at=utcnow())
    except HTTPException as e:
        _finish(task_id, status="failed", error=str(e.detail)[:500], finished_at=utcnow())
    except Exception as e:  # noqa: BLE001
        log.exception("tarea %s falló", task_id)
        _finish(task_id, status="failed", error=str(e)[:500], finished_at=utcnow())
