"""Calidad por fuente con consultas reales y pocas peticiones. Uso:
    .venv/bin/python scripts/source_quality.py [--limit 5] [--workers 4] [--only indeed linkedin] [--groups portal api ats]
        [--json salida.json] [--queries "analista soc|CO" "contador|MX"]
Imprime por fuente: nº de ofertas, % con descripción, % con salario, % con fecha, % con apply_url externo, % duplicadas
entre fuentes, segundos por consulta, peticiones y estado (ok/empty/blocked/error). Solo páginas públicas."""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.job import SearchQuery  # noqa: E402
from app.scout.quality import DEFAULT_QUERIES, QuerySpec, format_report, measure  # noqa: E402
from app.scout.registry import build_sources  # noqa: E402


def parse_query(spec: str) -> QuerySpec:
    """'texto|CO' o 'texto|remoto' -> consulta."""
    text, _, where = spec.partition("|")
    where = where.strip()
    remote = where.lower() in ("remoto", "remote", "")
    return QuerySpec(f"{text} / {where or 'remoto'}", SearchQuery(keywords=[text.strip()], countries=[] if remote else [where.upper()],
                                                                 languages=[], remote=remote))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=5, help="ofertas por fuente y consulta (pocas peticiones)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", nargs="*", help="prefijos de nombre de fuente")
    ap.add_argument("--groups", nargs="*", default=["portal", "api"], help="portal api ats careers")
    ap.add_argument("--queries", nargs="*", help="'texto|PAIS' (PAIS ISO o 'remoto'); por defecto las 4 estándar")
    ap.add_argument("--json", help="guarda el resultado completo en este archivo")
    a = ap.parse_args()
    logging.disable(logging.CRITICAL)
    sources = build_sources()
    if a.only:
        sources = [s for s in sources if any(s.name == o or s.name.startswith(o) for o in a.only)]
    queries = [parse_query(x) for x in a.queries] if a.queries else DEFAULT_QUERIES
    res = measure(sources, queries, limit=a.limit, workers=a.workers, groups=tuple(a.groups),
                  progress=lambda label, name, m: print(f"  [{label}] {name}: n={m['n']} {m['outcome']} {m['seconds']}s", file=sys.stderr))
    print(format_report(res))
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
