"""Tarea interna 'auto_tailor': pre-genera el CV adaptado de una oferta `apply` por política del usuario (la encola app/scheduling/automation).
No aplica ni envía nada. Vuelve a comprobar en el momento de ejecutar (la oferta pudo descartarse, la IA pudo apagarse) y delega en el
handler 'tailor', que es quien decide cómo degradar sin IA."""
from sqlmodel import select

from app.db import JobRow
from app.services.tasks import HANDLERS, TaskContext, register


@register("auto_tailor")
def auto_tailor(c: TaskContext) -> dict:
    if not c.params.get("auto"):
        raise ValueError("auto_tailor es una tarea interna de la automatización")
    from app.scheduling.automation import tailor_possible
    if not tailor_possible():
        raise ValueError("sin IA y sin modo extractivo: no se pre-genera el CV")
    row = c.session.exec(select(JobRow).where(JobRow.id == c.job_id, JobRow.user_id == c.user.id)).first()
    if row is None or row.status == "discarded":
        return {"skipped": "oferta descartada o inexistente"}
    return HANDLERS["tailor"](c)
