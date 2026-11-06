"""Mide el ranking contra ofertas reales etiquetadas. Uso:
   JH_EMBED_BACKEND=hash|auto .venv/bin/python scripts/calibrate_ranking.py [fixture.json] [--list]
Divide en 2 mitades (ids pares/impares) para detectar sobreajuste al afinar."""
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp()
os.environ.setdefault("JH_DATABASE_URL", f"sqlite:///{_tmp}/cal.db")

from sqlmodel import Session  # noqa: E402

from app.db import User, engine, init_db  # noqa: E402
from app.matcher import calibration as cal  # noqa: E402


def main() -> None:
    path = next((a for a in sys.argv[1:] if not a.startswith("--")), "tests/fixtures_rank/soc_real.json")
    init_db()
    profile, rows = cal.load(path)
    with Session(engine) as s:
        u = User(email="cal@x.com", password_hash="x")
        s.add(u)
        s.commit()
        s.refresh(u)
        scored = cal.score_all(profile, rows, s, u.id)
    print("backend embeddings:", os.environ.get("JH_EMBED_BACKEND", "auto"))
    print("TODO   ", cal.report(scored))
    print("pares  ", cal.report([x for x in scored if x.id % 2 == 0]))
    print("impares", cal.report([x for x in scored if x.id % 2 == 1]))
    if "--list" in sys.argv:
        for x in sorted(scored, key=lambda t: -t.score):
            print(f"  L{x.label} s1={x.stage1:5.1f} ev={x.score:.2f} {x.rec:8s} {x.title[:60]}")


if __name__ == "__main__":
    main()
