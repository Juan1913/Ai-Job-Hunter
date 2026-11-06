"""Canario en vivo: una búsqueda mínima por fuente. Uso:
    .venv/bin/python scripts/verify_sources.py [--only indeed linkedin] [--no-persist] [--workers 3]
Imprime una tabla ok / vacía / bloqueada / degradada (0 resultados silenciosos) / error y la guarda en SourceHealth.

    .venv/bin/python scripts/verify_sources.py --probe-indeed-age 14
hace UNA petición a Indeed MX con `fromage=14` y dice si lo acepta (cómo se verificó qué valores son válidos)."""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import SQLModel  # noqa: E402

from app.db import engine  # noqa: E402
from app.scout.health import format_table, run_canary  # noqa: E402


def probe_indeed_age(days: int) -> int:
    from app.scout import fetch
    from app.scout.sources.indeed import parse_cards
    url = f"https://mx.indeed.com/jobs?q=analista&l=&fromage={days}"
    try:
        n = len(parse_cards(fetch.get_html(url)))
        print(f"fromage={days}: HTTP 200, {n} tarjetas")
        return 0
    except fetch.FetchError as e:
        print(f"fromage={days}: RECHAZADO ({e})")
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="*", help="prefijos de nombre de fuente")
    ap.add_argument("--no-persist", action="store_true")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--probe-indeed-age", type=int, metavar="DIAS", help="una petición a Indeed MX con fromage=DIAS")
    a = ap.parse_args()
    logging.disable(logging.CRITICAL)
    if a.probe_indeed_age is not None:
        return probe_indeed_age(a.probe_indeed_age)
    SQLModel.metadata.create_all(engine)
    res = run_canary(only=a.only, persist=not a.no_persist, max_workers=a.workers)
    print(format_table(res))
    bad = [r for r in res if r["status"] != "ok"]
    print(f"\n{len(res) - len(bad)}/{len(res)} ok")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
