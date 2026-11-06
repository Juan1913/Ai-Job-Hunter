"""Latido del trabajador en BD y lectura de su estado (para /ready, el healthcheck del contenedor y las métricas)."""
from __future__ import annotations

import os
import socket
from datetime import datetime, timedelta

from sqlmodel import Session, col, select

from app.config import settings
from app.db import WorkerHeartbeat, engine, utcnow
from app.worker import queue


def default_worker_id() -> str:
    return settings.worker_id or f"{socket.gethostname()}-{os.getpid()}"


def beat(worker_id: str, *, state: str = "running", running: int = 0, concurrency: int = 0, stats: dict | None = None,
         started_at: datetime | None = None, now: datetime | None = None) -> None:
    now = now or utcnow()
    with Session(engine) as s:
        hb = s.get(WorkerHeartbeat, worker_id)
        if hb is None:
            hb = WorkerHeartbeat(worker_id=worker_id, host=socket.gethostname(), pid=os.getpid(), started_at=started_at or now)
        hb.state, hb.running, hb.concurrency, hb.beat_at = state, running, concurrency, now
        if stats is not None:
            hb.stats = stats
        s.add(hb)
        s.commit()


def alive_workers(*, now: datetime | None = None, max_age_s: float | None = None, host: str | None = None) -> list[WorkerHeartbeat]:
    """Trabajadores con latido reciente y que no están parados."""
    now = now or utcnow()
    since = now - timedelta(seconds=settings.worker_alive_s if max_age_s is None else max_age_s)
    with Session(engine) as s:
        stmt = select(WorkerHeartbeat).where(col(WorkerHeartbeat.beat_at) >= since, WorkerHeartbeat.state != "stopped")
        if host:
            stmt = stmt.where(WorkerHeartbeat.host == host)
        return list(s.exec(stmt).all())


def status(*, now: datetime | None = None) -> dict:
    """Resumen para /ready: ok = hay al menos un trabajador vivo."""
    now = now or utcnow()
    try:
        alive = alive_workers(now=now)
        last = None
        with Session(engine) as s:
            last = s.exec(select(WorkerHeartbeat).order_by(col(WorkerHeartbeat.beat_at).desc())).first()
        age = None
        if last:
            b = last.beat_at if last.beat_at.tzinfo else last.beat_at.replace(tzinfo=now.tzinfo)
            age = round((now - b).total_seconds(), 1)
        q = queue.depth(now)
        res = {"ok": bool(alive), "alive": len(alive), "last_beat_age_s": age, "queued": q["queued"], "running": q["running"], "dead": q["dead"],
               "oldest_ready_age_s": round(q["oldest_ready_age_s"], 1)}
        if not alive:
            res["error"] = "ningún trabajador con latido reciente" + (f" (último hace {age:.0f} s)" if age is not None else " (nunca arrancó)")
        return res
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}


def purge(*, now: datetime | None = None, days: int = 1) -> int:
    """Borra latidos de trabajadores que no laten desde hace `days` (contenedores que ya no existen)."""
    now = now or utcnow()
    with Session(engine) as s:
        old = s.exec(select(WorkerHeartbeat).where(col(WorkerHeartbeat.beat_at) < now - timedelta(days=days))).all()
        for hb in old:
            s.delete(hb)
        s.commit()
    return len(old)
