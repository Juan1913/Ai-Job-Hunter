"""Respaldo periódico de SQLite con la API de backup (consistente aunque la API esté escribiendo).

Variables: DB_PATH (/data/jobhunter.db), BACKUP_DIR (/backups), INTERVAL_HOURS (6), KEEP (14).
Cada copia se verifica con PRAGMA integrity_check antes de conservarla y se comprime con gzip.
"""
import gzip
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DB = Path(os.environ.get("DB_PATH", "/data/jobhunter.db"))
OUT = Path(os.environ.get("BACKUP_DIR", "/backups"))
EVERY = float(os.environ.get("INTERVAL_HOURS", "6")) * 3600
KEEP = int(os.environ.get("KEEP", "14"))


def snapshot() -> Path | None:
    if not DB.exists():
        print(f"[backup] {DB} aún no existe, se omite", flush=True)
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tmp = OUT / f".tmp-{stamp}.db"
    src = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("la copia no pasa integrity_check")
    finally:
        src.close()
        dst.close()
    final = OUT / f"jobhunter-{stamp}.db.gz"
    with open(tmp, "rb") as f_in, gzip.open(final, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    tmp.unlink()
    return final


def rotate() -> None:
    files = sorted(OUT.glob("jobhunter-*.db.gz"))
    for old in files[:-KEEP] if KEEP > 0 else []:
        old.unlink()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            f = snapshot()
            if f:
                rotate()
                print(f"[backup] ok {f.name} ({f.stat().st_size // 1024} KiB)", flush=True)
        except Exception as e:  # noqa: BLE001  el job nunca muere: reintenta en el siguiente ciclo
            print(f"[backup] ERROR {e}", file=sys.stderr, flush=True)
        if os.environ.get("ONCE"):
            return
        time.sleep(EVERY)


if __name__ == "__main__":
    main()
