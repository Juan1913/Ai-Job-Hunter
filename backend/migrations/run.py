"""Migra la base de datos a `head` antes de arrancar la API (lo llama docker-entrypoint.sh).

Caso especial: una BD creada antes por `create_all` (sin tabla alembic_version) se «adopta»:
se marca con la revisión inicial y se aplican las migraciones siguientes.
"""
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import engine  # noqa: E402


def main() -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    tables = set(inspect(engine).get_table_names())
    if "user" in tables and "alembic_version" not in tables:
        base = ScriptDirectory.from_config(cfg).get_base()
        print(f"[migrate] BD preexistente sin versionar: se marca en {base}", flush=True)
        command.stamp(cfg, base)
    command.upgrade(cfg, "head")
    print("[migrate] esquema al día", flush=True)


if __name__ == "__main__":
    main()
