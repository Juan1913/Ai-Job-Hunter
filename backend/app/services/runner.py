"""Ejecuta búsquedas en segundo plano y persiste ofertas con deduplicación por usuario."""
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, or_, select

from app.config import settings
from app.db import Company, JobEvent, JobRow, ProfileRow, SearchRun, engine, utcnow
from app.domain.job import SearchQuery
from app.domain.profile import Profile
from app.matcher.service import build_scorer
from app.scout.base import supports
from app.scout.registry import build_sources
from app.scout.service import run_search

log = logging.getLogger(__name__)
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="search")
_lock = Lock()
_score_lock = Lock()


def start_search(session: Session, user_id: int, params: dict) -> SearchRun:
    run = SearchRun(user_id=user_id, params=params)
    session.add(run)
    session.commit()
    session.refresh(run)
    if settings.run_inline:
        execute_run(run.id)
    elif settings.worker_external:
        from app.worker import queue  # la ejecuta `python -m app.worker`; la API no gasta hilos propios
        queue.enqueue(session, "search", ref_id=run.id, user_id=user_id, payload={"run_id": run.id}, priority=10, dedup_key=f"search:{run.id}")
    else:
        _pool.submit(execute_run, run.id)
    return run


def _update(run_id: int, **fields) -> None:
    with Session(engine) as s:
        run = s.get(SearchRun, run_id)
        for k, v in fields.items():
            setattr(run, k, v)
        s.add(run)
        s.commit()


def _persist(run_id: int, user_id: int, profile: Profile, q: SearchQuery, jobs: list) -> int:
    """Puntúa (etapa 1, sin LLM) y guarda las ofertas nuevas del usuario. Devuelve cuántas eran nuevas.
    Se serializa dentro del proceso y se reintenta ante una carrera de inserción: dos búsquedas de usuarios distintos sobre las mismas ofertas
    (caso normal con la caché compartida y el trabajador) calculan los mismos embeddings a la vez y chocan en EmbeddingCache.key."""
    for attempt in range(3):
        try:
            with _score_lock, Session(engine) as s:
                new = 0
                scorer = build_scorer(s, user_id, profile, q.keywords)     # etapa 1 híbrida (léxico + embeddings + filtros), sin LLM
                scorer.prefetch(jobs)
                for j in jobs:
                    row = s.exec(select(JobRow).where(JobRow.user_id == user_id, JobRow.dedup_key == j.dedup_key)).first()
                    if row:
                        row.last_seen = utcnow()
                        s.add(row)
                        continue
                    row = JobRow(user_id=user_id, dedup_key=j.dedup_key, data=j.model_dump(), run_id=run_id,
                                 stage1=scorer.score(j).score)
                    s.add(row)
                    s.flush()
                    s.add(JobEvent(user_id=user_id, job_id=row.id, kind="discovered", note=j.source))
                    new += 1
                s.commit()
                return new
        except IntegrityError:
            if attempt == 2:
                raise
            log.warning("carrera de inserción al puntuar la búsqueda %s: reintento %s", run_id, attempt + 2)
    return 0


def execute_run(run_id: int) -> None:
    try:
        with Session(engine) as s:
            run = s.get(SearchRun, run_id)
            user_id, params = run.user_id, dict(run.params)
            prow = s.get(ProfileRow, user_id)
            profile = Profile.model_validate(prow.data)
            comps = s.exec(select(Company).where(Company.active, or_(Company.user_id == user_id, Company.user_id == None))).all()
            company_dicts = [{"company": c.name, "kind": c.kind, "token": c.token, "url": c.url, "stealth": c.stealth} for c in comps]
        pr = profile.preferences
        extra = {k: params[k] for k in ("hours_old", "employment_types", "work_modes") if params.get(k)}      # búsquedas programadas: filtros propios
        q = SearchQuery(keywords=params.get("keywords") or pr.target_roles or [profile.headline], countries=params.get("countries") or pr.countries,
                        regions=params.get("regions") or pr.regions, languages=params.get("languages") or pr.languages,
                        remote=params.get("remote", pr.remote), limit_per_source=params.get("limit_per_source", 25), **extra)
        sources = [src for src in build_sources(company_dicts or None) if supports(src, q)]
        # Caché compartida entre usuarios (solo búsquedas programadas, salvo JH_CACHE_MANUAL) y presupuesto de peticiones por hora/dominio.
        from app.scheduling.cache import wrap_sources
        sources, skipped = wrap_sources(sources, use_cache=bool(params.get("scheduled")) or settings.cache_manual)
        _update(run_id, status="running", params={**params, "keywords_used": list(q.keywords)},
                sources={**{s_.name: {"status": "pending"} for s_ in sources}, **skipped})
        report: dict = {}

        def on_source(name: str, found: int, err: str | None, outcome: str | None = None) -> None:
            # `status` (done|error) como siempre; `outcome` (ok|empty|blocked|error) distingue "bloqueada" de "vacía"
            with _lock:
                with Session(engine) as s2:
                    r = s2.get(SearchRun, run_id)
                    rep = report.get(name, {})      # lo rellena run_search justo antes de avisar: tiempo y muestra de títulos para la vista previa
                    r.sources = {**r.sources, name: {"status": "error" if err else "done", "found": found, "error": err, "outcome": outcome,
                                                     "seconds": rep.get("seconds"), "sample": rep.get("sample") or []}}
                    s2.add(r)
                    s2.commit()

        jobs, _ = run_search(q, sources, on_source, report)
        new = _persist(run_id, user_id, profile, q, jobs)
        _update(run_id, status="done", new_jobs=new, finished_at=utcnow())
    except Exception as e:
        log.exception("búsqueda %s falló", run_id)
        _update(run_id, status="failed", error=str(e)[:500], finished_at=utcnow())
    _after_run(run_id)


def _after_run(run_id: int) -> None:
    """Automatización por política (evaluar lo nuevo, avisar, pre-generar CV) y contabilidad de la búsqueda programada. Nunca rompe la búsqueda."""
    try:
        from app.scheduling import automation
        automation.after_run(run_id)
    except Exception:  # noqa: BLE001
        log.exception("automatización posterior a la búsqueda %s falló", run_id)
