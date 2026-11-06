"""Tareas base: analizar la oferta, adaptar el CV y escribir la carta."""
from app.services.tasks import TaskContext, register


@register("analyze", quota="analyze")
def analyze(c: TaskContext) -> dict:
    from app.api import jobs as api
    return api.analyze(c.job_id, bool(c.params.get("force")), c.user, c.session, c.llm).model_dump(mode="json")


@register("tailor", quota="tailor")
def tailor(c: TaskContext) -> dict:
    from app.api import jobs as api
    body = api.TailorIn(confirmed=c.params.get("confirmed", []), save_confirmed=c.params.get("save_confirmed", True))
    return api.tailor(c.job_id, body, c.user, c.session, c.llm, c.writer).model_dump(mode="json")


@register("cover_letter")
def cover_letter(c: TaskContext) -> dict:
    from app.api import jobs as api
    body = api.CoverLetterIn(confirmed=c.params.get("confirmed", []), lang=c.params.get("lang"))
    return api.cover_letter(c.job_id, body, c.user, c.session, c.llm, c.writer)
