"""Tarea en segundo plano 'prep': historias STAR, preguntas probables, preguntas al entrevistador, negociación y plan de mejora."""
from app.services.tasks import TaskContext, register


@register("prep")
def prep(c: TaskContext) -> dict:
    from app.api import jobs as api
    from app.api.deps import load_profile
    from app.domain.tailoring import KeywordGap
    from app.prep import service

    row = api._own(c.session, c.user, c.job_id)
    if row.gaps is None:                                  # las brechas salen del análisis (cacheado): sin él no hay qué preparar
        api.analyze(c.job_id, False, c.user, c.session, c.llm)
        c.session.refresh(row)
    profile = load_profile(c.session, c.user)
    gaps = [KeywordGap.model_validate(g) for g in (row.gaps or [])]
    res = service.run(c.session, c.user, row, profile, gaps, lang=c.params.get("lang"), parts=c.params.get("parts"), force=bool(c.params.get("force")),
                      use_llm=bool(c.params.get("use_llm")), llm=c.writer, salary=service.salary_of(c.session, c.user, c.job_id, c.params))
    return {"job_id": c.job_id, "parts": {k: {"cached": v["cached"]} for k, v in res.items()},
            "stories": len(res.get("stories", {}).get("data", {}).get("stories", [])) if "stories" in res else None}
