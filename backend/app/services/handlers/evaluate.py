"""Tarea 'evaluate': calcula el informe de evaluación de una oferta o, con params['batch'], de todas las activas del usuario.
Es determinista; solo usa el LLM ligero (una llamada cacheada) cuando las regex no sacan años/nivel/idiomas de una descripción larga."""
from sqlmodel import select

from app.api.deps import load_profile
from app.db import JobRow
from app.evaluation import service
from app.services.tasks import TaskContext, register


@register("evaluate", quota="evaluate")
def evaluate(c: TaskContext) -> dict:
    profile = load_profile(c.session, c.user)
    force, lang = bool(c.params.get("force")), c.params.get("lang")
    if c.params.get("batch"):
        return service.evaluate_all(c.session, c.user, profile, llm=c.llm, force=force, lang=lang)
    row = c.session.exec(select(JobRow).where(JobRow.id == c.job_id, JobRow.user_id == c.user.id)).first()
    if row is None:
        raise ValueError("oferta no encontrada")
    return service.evaluate_row(c.session, row, profile, lang=lang, force=force, llm=c.llm).model_dump(mode="json")
