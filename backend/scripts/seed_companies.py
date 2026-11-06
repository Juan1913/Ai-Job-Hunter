"""Siembra idempotente de empresas GLOBALES (Company.user_id NULL) desde app/scout/seeds.py.
    .venv/bin/python scripts/seed_companies.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, SQLModel, select  # noqa: E402

from app.db import Company, engine, utcnow  # noqa: E402
from app.scout.seeds import SEED_COMPANIES  # noqa: E402


def seed(session: Session) -> tuple[int, int]:
    created = updated = 0
    for c in SEED_COMPANIES:
        row = session.exec(select(Company).where(Company.user_id == None, Company.name == c["company"])).first()  # noqa: E711
        fields = {"kind": c["kind"], "token": c.get("token"), "url": c.get("url"), "stealth": c.get("stealth", False), "active": True}
        if row is None:
            session.add(Company(user_id=None, name=c["company"], verified_at=utcnow(), **fields))
            created += 1
        elif any(getattr(row, k) != v for k, v in fields.items()):
            for k, v in fields.items():
                setattr(row, k, v)
            session.add(row)
            updated += 1
    session.commit()
    return created, updated


if __name__ == "__main__":
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        print("creadas: {}, actualizadas: {}".format(*seed(s)))
