from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from app.api.deps import check_quota, current_user, get_llm, get_writer
from app.api.jobs import _own
from app.db import JobTask, User, get_session
from app.services.tasks import QUOTA_KIND, kinds, start_task

router = APIRouter(prefix="/me", tags=["tasks"])


class TaskIn(BaseModel):
    kind: str = Field(description="analyze | tailor | cover_letter | (tipos registrados en app/services/handlers)")
    confirmed: list[str] = []          # requisitos 'missing' que el usuario confirma tener
    save_confirmed: bool = True
    force: bool = False                # analyze: ignorar caché
    lang: str | None = None            # cover_letter: 'es' | 'en'


def _out(t: JobTask) -> dict:
    return {"id": t.id, "job_id": t.job_id, "kind": t.kind, "status": t.status, "result": t.result, "error": t.error,
            "created_at": t.created_at.isoformat(), "finished_at": t.finished_at.isoformat() if t.finished_at else None}


@router.post("/jobs/{job_id}/tasks", status_code=202)
def create_task(job_id: int, body: TaskIn, user: User = Depends(current_user), session: Session = Depends(get_session),
                llm=Depends(get_llm), writer=Depends(get_writer)):
    if body.kind not in kinds():
        raise HTTPException(422, f"kind debe ser uno de: {', '.join(kinds())}")
    _own(session, user, job_id)
    if body.kind in QUOTA_KIND:
        check_quota(QUOTA_KIND[body.kind], user, session)
    params = body.model_dump(exclude={"kind"})
    return _out(start_task(session, user, job_id, body.kind, params, llm, writer))


@router.get("/tasks")
def list_tasks(active: bool = False, user: User = Depends(current_user), session: Session = Depends(get_session)):
    stmt = select(JobTask).where(JobTask.user_id == user.id)
    if active:
        stmt = stmt.where(col(JobTask.status).in_(["queued", "running"]))
    return [_out(t) for t in session.exec(stmt.order_by(col(JobTask.id).desc()).limit(50)).all()]


@router.get("/tasks/{task_id}")
def get_task(task_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    t = session.get(JobTask, task_id)
    if not t or t.user_id != user.id:
        raise HTTPException(404, "Tarea no encontrada")
    return _out(t)
