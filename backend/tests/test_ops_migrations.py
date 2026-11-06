"""El esquema de Alembic debe coincidir con los modelos: así `alembic upgrade head` en producción
(donde db_auto_create=False) no deja tablas sin crear cuando un módulo añade modelos."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_alembic_head_matches_models():
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "JH_DATABASE_URL": f"sqlite:///{d}/m.db"}
        up = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, capture_output=True, text=True)
        assert up.returncode == 0, up.stderr
        code = (
            "from alembic.migration import MigrationContext\n"
            "from alembic.autogenerate import compare_metadata\n"
            "from sqlmodel import SQLModel\n"
            "import app.db, app.auth_models\n"
            "from app.db import engine\n"
            "with engine.connect() as c:\n"
            "    diff = compare_metadata(MigrationContext.configure(c), SQLModel.metadata)\n"
            "print('DIFF', diff)\n"
            "raise SystemExit(1 if diff else 0)\n")
        chk = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
        assert chk.returncode == 0, f"faltan migraciones (alembic revision --autogenerate): {chk.stdout[-800:]}{chk.stderr[-400:]}"
