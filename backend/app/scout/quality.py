"""Medición de calidad PROPIA de las fuentes (no depende de ningún tercero).

Por cada (consulta, fuente) se ejecuta la búsqueda real con pocas peticiones y se mide sobre lo que de verdad llega al usuario
(tras `prepare`: normalizado, sin cerradas, con los filtros):
  n            ofertas devueltas
  %desc        con descripción útil (>= 80 caracteres)
  %salario     con salario numérico (o texto de salario)
  %fecha       con fecha de publicación normalizada
  %apply_ext   con URL de aplicar EXTERNA (otro dominio que el de la oferta/portal): llegar al sitio real de la empresa
  %dup         duplicadas en OTRA fuente de la misma consulta (misma `dedup_key`): redundancia entre fuentes
  seg / peticiones / estado (ok | empty | blocked | error)
Uso desde scripts/source_quality.py. Las funciones puras (`source_metrics`, `duplicate_keys`) se prueban sin red."""
import functools
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.domain.job import Job, SearchQuery
from app.scout import fetch, health
from app.scout.base import supports
from app.scout.query import expand_query

MIN_DESC = 80


@dataclass
class QuerySpec:
    label: str
    query: SearchQuery


DEFAULT_QUERIES = [
    QuerySpec("analista soc / Colombia", SearchQuery(keywords=["analista soc"], countries=["CO"], languages=["es"], remote=True)),
    QuerySpec("contador / México", SearchQuery(keywords=["contador"], countries=["MX"], languages=["es"], remote=True)),
    QuerySpec("software engineer / remoto", SearchQuery(keywords=["software engineer"], remote=True)),
    QuerySpec("enfermera / Chile", SearchQuery(keywords=["enfermera"], countries=["CL"], languages=["es"], remote=False)),
]


def _root(url: str | None) -> str:
    host = (urlsplit(url or "").hostname or "").lower()
    return ".".join(host.split(".")[-2:]) if host else ""


def has_external_apply(j: Job) -> bool:
    """La URL de aplicar existe y lleva a un dominio distinto del de la oferta (sitio real de la empresa / su ATS)."""
    return bool(j.apply_url) and _root(j.apply_url) != _root(j.url)


def has_salary(j: Job) -> bool:
    return j.salary_min is not None or j.salary_annual_min is not None or bool(j.salary)


def duplicate_keys(by_source: dict[str, list[Job]]) -> dict[str, set[str]]:
    """Por fuente: dedup_keys que TAMBIÉN aparecen en otra fuente."""
    where: dict[str, set[str]] = defaultdict(set)
    for src, jobs in by_source.items():
        for j in jobs:
            where[j.dedup_key].add(src)
    return {src: {j.dedup_key for j in jobs if len(where[j.dedup_key]) > 1} for src, jobs in by_source.items()}


def source_metrics(jobs: list[Job], dup: set[str] | None = None) -> dict:
    n = len(jobs)

    def pct(k: int) -> float | None:
        return round(100 * k / n, 1) if n else None
    return {"n": n,
            "desc": pct(sum(1 for j in jobs if len(j.description) >= MIN_DESC)),
            "salary": pct(sum(1 for j in jobs if has_salary(j))),
            "date": pct(sum(1 for j in jobs if j.posted_at)),
            "apply_ext": pct(sum(1 for j in jobs if has_external_apply(j))),
            "dup": pct(sum(1 for j in jobs if j.dedup_key in (dup or set())))}


@dataclass
class Cell:
    jobs: list[Job] = field(default_factory=list)
    seconds: float = 0.0
    requests: int = 0
    outcome: str = "ok"
    error: str | None = None


def run_cell(source, query: SearchQuery) -> Cell:
    """Una fuente, una consulta: hilo propio (los contadores de fetch son por hilo)."""
    from app.scout.service import prepare, search_source
    t0 = time.monotonic()
    fetch.reset_events()
    cell = Cell()
    try:
        raw = search_source(source, query)
        cell.jobs = prepare(raw, query)
        cell.outcome = health.outcome_of(len(raw), None, fetch.events())
    except Exception as e:  # noqa: BLE001
        ev = fetch.events()
        cell.outcome = "blocked" if isinstance(e, fetch.Blocked) or (ev["blocked"] and not ev["ok"]) else "error"
        cell.error = f"{type(e).__name__}: {e}"[:200]
    finally:
        fetch.close_session()
    cell.seconds = time.monotonic() - t0
    cell.requests = fetch.events()["requests"]
    return cell


def _cell_for(query: SearchQuery, source) -> Cell:
    return run_cell(source, query)


def measure(sources: list, queries: list[QuerySpec] | None = None, *, limit: int = 5, workers: int = 4,
            groups: tuple[str, ...] = ("portal", "api"), progress=None) -> dict:
    """Devuelve {"queries": {label: {fuente: {metrics...}}}, "sources": {fuente: {agregado}}}."""
    queries = queries or DEFAULT_QUERIES
    result: dict = {"queries": {}, "sources": {}}
    totals: dict[str, list[Job]] = defaultdict(list)
    seconds: dict[str, float] = Counter()
    requests: dict[str, int] = Counter()
    outcomes: dict[str, Counter] = defaultdict(Counter)
    runs: Counter = Counter()
    dup_total: Counter = Counter()
    for spec in queries:
        q = expand_query(spec.query.model_copy(update={"limit_per_source": limit}))     # como en producción: con variantes
        active = [s for s in sources if getattr(s, "group", "portal") in groups and supports(s, q)]
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="quality") as ex:
            cells = dict(zip((s.name for s in active), ex.map(functools.partial(_cell_for, q), active)))
        dups = duplicate_keys({name: c.jobs for name, c in cells.items()})
        rows = {}
        for name, c in cells.items():
            m = source_metrics(c.jobs, dups[name])
            m.update(seconds=round(c.seconds, 1), requests=c.requests, outcome=c.outcome, error=c.error)
            rows[name] = m
            totals[name] += c.jobs
            seconds[name] += c.seconds
            requests[name] += c.requests
            outcomes[name][c.outcome] += 1
            runs[name] += 1
            dup_total[name] += len(dups[name] & {j.dedup_key for j in c.jobs})
            if progress:
                progress(spec.label, name, m)
        result["queries"][spec.label] = rows
    for name, jobs in totals.items():
        m = source_metrics(jobs)
        m["dup"] = round(100 * dup_total[name] / len(jobs), 1) if jobs else None
        m.update(seconds=round(seconds[name] / runs[name], 1), requests=requests[name], runs=runs[name], outcomes=dict(outcomes[name]))
        result["sources"][name] = m
    return result


def format_report(result: dict) -> str:
    def f(v):
        return "-" if v is None else (f"{v:g}" if isinstance(v, (int, float)) else str(v))
    head = ("fuente", "n", "%desc", "%sal", "%fecha", "%apply_ext", "%dup", "seg/consulta", "pet.", "estado")
    rows = [head]
    for name, m in sorted(result["sources"].items()):
        rows.append((name, f(m["n"]), f(m["desc"]), f(m["salary"]), f(m["date"]), f(m["apply_ext"]), f(m["dup"]), f(m["seconds"]), f(m["requests"]),
                     "/".join(f"{k}:{v}" for k, v in sorted(m["outcomes"].items()))))
    w = [max(len(r[i]) for r in rows) for i in range(len(head))]
    return "\n".join("  ".join(c.ljust(w[i]) for i, c in enumerate(r)) for r in rows)
