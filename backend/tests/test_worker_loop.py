"""Bucle del trabajador: cola de búsquedas/tareas desde la API, /ready, recuperación tras caída, apagado ordenado y SIGTERM real."""
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

from sqlmodel import Session, select

from app.config import settings
from app.db import WorkerHeartbeat, WorkItem, engine, utcnow
from app.worker import health, queue
from app.worker.loop import Worker
from tests.shared import API
from tests.test_worker_support import sim  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]


def _external(monkeypatch, llm):
    monkeypatch.setattr(settings, "run_inline", False)
    monkeypatch.setattr(settings, "worker_external", True)
    monkeypatch.setattr("app.llm.factory.build_llm", lambda role="fast", user_id=None, access=None: llm)   # el trabajador crea sus clientes con la fábrica


def test_api_only_enqueues_and_the_worker_executes_search_and_tasks(client, ready, llm, monkeypatch):
    _external(monkeypatch, llm)
    r = client.post(f"{API}/me/searches", json={}, headers=ready)
    assert r.status_code == 202 and r.json()["status"] == "queued"          # la API no la ejecutó
    with Session(engine) as s:
        assert [i.kind for i in s.exec(select(WorkItem)).all()] == ["search"]
    w = Worker("w-test", concurrency=2)
    w.run_until_idle()
    run = client.get(f"{API}/me/searches/{r.json()['id']}", headers=ready).json()
    assert run["status"] == "done" and run["new_jobs"] == 2

    jid = client.get(f"{API}/me/jobs", headers=ready).json()["items"][0]["id"]
    t = client.post(f"{API}/me/jobs/{jid}/tasks", json={"kind": "analyze"}, headers=ready)
    assert t.status_code == 202 and t.json()["status"] == "queued"
    again = client.post(f"{API}/me/jobs/{jid}/tasks", json={"kind": "analyze"}, headers=ready)
    assert again.json()["id"] == t.json()["id"]                              # idempotente también con cola
    w.run_until_idle()
    done = client.get(f"{API}/me/tasks/{t.json()['id']}", headers=ready).json()
    assert done["status"] == "done" and done["result"]["match"] > 0
    with Session(engine) as s:
        assert {i.state for i in s.exec(select(WorkItem)).all()} == {"done"}


def test_without_external_worker_behaviour_is_unchanged(client, ready):
    r = client.post(f"{API}/me/searches", json={}, headers=ready)           # JH_RUN_INLINE: se ejecuta en el acto
    assert client.get(f"{API}/me/searches/{r.json()['id']}", headers=ready).json()["status"] == "done"
    with Session(engine) as s:
        assert s.exec(select(WorkItem)).all() == []


def test_ready_reflects_a_live_worker(client, monkeypatch):
    monkeypatch.setattr(settings, "ready_requires_llm", False)
    assert "worker" not in client.get("/ready").json()["checks"]            # sin trabajador externo no se exige
    monkeypatch.setattr(settings, "worker_external", True)
    r = client.get("/ready")
    assert r.status_code == 503 and r.json()["checks"]["worker"]["ok"] is False
    health.beat("w1", concurrency=2)
    r = client.get("/ready")
    assert r.status_code == 200 and r.json()["checks"]["worker"]["alive"] == 1
    with Session(engine) as s:                                              # latido viejo = caído
        hb = s.get(WorkerHeartbeat, "w1")
        hb.beat_at = utcnow() - timedelta(seconds=settings.worker_alive_s + 5)
        s.add(hb)
        s.commit()
    assert client.get("/ready").status_code == 503


def test_crash_recovery_a_second_worker_finishes_what_the_dead_one_claimed(client, ready, llm, monkeypatch, sim):
    _external(monkeypatch, llm)
    rid = client.post(f"{API}/me/searches", json={}, headers=ready).json()["id"]
    sim.advance(seconds=5)
    dead = queue.claim("w-muerto", now=sim.now(), lease_s=60)                # reclama y "se cae" sin completar
    assert len(dead) == 1
    sim.advance(seconds=120)                                                 # el lease venció
    w = Worker("w-vivo", clock=sim.now)
    w.run_until_idle()
    assert client.get(f"{API}/me/searches/{rid}", headers=ready).json()["status"] == "done"
    assert w.stats["recovered"] == 1
    with Session(engine) as s:
        it = s.exec(select(WorkItem)).one()
        assert it.state == "done" and it.attempts == 2


def test_dead_letter_marks_the_search_failed(client, ready, llm, monkeypatch, sim):
    _external(monkeypatch, llm)
    rid = client.post(f"{API}/me/searches", json={}, headers=ready).json()["id"]
    with Session(engine) as s:
        it = s.exec(select(WorkItem)).one()
        it.max_attempts = 1
        s.add(it)
        s.commit()
    sim.advance(seconds=5)
    queue.claim("w-muerto", now=sim.now(), lease_s=10)
    sim.advance(seconds=60)
    Worker("w-vivo", clock=sim.now).tick()
    run = client.get(f"{API}/me/searches/{rid}", headers=ready).json()
    assert run["status"] == "failed" and "abandonada" in run["error"]


def test_failing_handler_retries_with_backoff_and_ends_dead(sim):
    calls = []

    def boom(item):
        calls.append(item.attempts)
        raise RuntimeError("fallo transitorio")

    from app.worker.handlers import Kind
    with Session(engine) as s:
        queue.enqueue(s, "x", ref_id=1, max_attempts=3, now=sim.now())
    w = Worker("w", clock=sim.now, kinds={"x": Kind(boom, lambda item, err: calls.append("dead"))})
    for _ in range(3):
        sim.advance(minutes=30)
        w.run_until_idle()
    assert calls == [1, 2, 3, "dead"]
    with Session(engine) as s:
        it = s.exec(select(WorkItem)).one()
        assert it.state == "dead" and "fallo transitorio" in it.last_error


def test_concurrency_limit_and_graceful_shutdown_releases_unfinished_work(sim, monkeypatch):
    import threading
    gate = threading.Event()
    started = []

    def slow(item):
        started.append(item.id)
        gate.wait(10)

    from app.worker.handlers import Kind
    with Session(engine) as s:
        for i in range(5):
            queue.enqueue(s, "slow", ref_id=i, now=sim.now())
    monkeypatch.setattr(settings, "worker_grace_s", 0.3)
    w = Worker("w", concurrency=2, clock=sim.now, kinds={"slow": Kind(slow, lambda *_: None)})
    sim.advance(seconds=1)
    assert w.tick() == 2 and w.tick() == 0                                   # respeta la concurrencia
    w.shutdown()                                                             # no terminan dentro de la gracia
    gate.set()
    with Session(engine) as s:
        its = s.exec(select(WorkItem)).all()
        assert all(i.state == "queued" and i.attempts == 0 and i.locked_by is None for i in its)   # devueltos sin gastar intento
        assert s.get(WorkerHeartbeat, "w").state == "stopped"


def test_real_process_stops_cleanly_on_sigterm():
    """`python -m app.worker` de verdad: arranca, late en BD y sale con código 0 al recibir SIGTERM, dejando el latido en 'stopped'."""
    d = tempfile.mkdtemp()
    env = {**os.environ, "JH_DATABASE_URL": f"sqlite:///{d}/w.db", "JH_WORKER_POLL_S": "0.2", "JH_WORKER_HEARTBEAT_S": "0.2", "JH_LOG_JSON": "false",
           "JH_DB_AUTO_CREATE": "true", "PYTHONPATH": str(ROOT)}
    p = subprocess.Popen([sys.executable, "-m", "app.worker", "--id", "proc-1"], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        import sqlite3
        deadline = time.time() + 40
        state = None
        while time.time() < deadline:
            try:
                c = sqlite3.connect(f"{d}/w.db")
                row = c.execute("select state from workerheartbeat where worker_id='proc-1'").fetchone()
                c.close()
                if row:
                    state = row[0]
                    break
            except sqlite3.Error:
                pass
            time.sleep(0.3)
        assert state == "running", p.poll()
        hc = subprocess.run([sys.executable, "-m", "app.worker", "--healthcheck"], cwd=ROOT, env=env, capture_output=True, text=True)
        assert hc.returncode == 0, hc.stdout + hc.stderr
        p.send_signal(signal.SIGTERM)
        assert p.wait(timeout=30) == 0
        c = sqlite3.connect(f"{d}/w.db")
        assert c.execute("select state from workerheartbeat where worker_id='proc-1'").fetchone()[0] == "stopped"
        c.close()
        hc2 = subprocess.run([sys.executable, "-m", "app.worker", "--healthcheck"], cwd=ROOT, env=env, capture_output=True, text=True)
        assert hc2.returncode == 1                                           # parado: ya no hay latido válido
    finally:
        if p.poll() is None:
            p.kill()


def test_unknown_item_kind_goes_to_dead_without_retry(sim):
    with Session(engine) as s:
        queue.enqueue(s, "no-existe", ref_id=1, now=sim.now())
    Worker("w", clock=sim.now).run_until_idle()
    with Session(engine) as s:
        assert s.exec(select(WorkItem)).one().state == "dead"


def test_stale_heartbeats_are_purged_but_live_ones_are_kept(sim):
    health.beat("viejo", now=sim.now() - timedelta(days=3))
    health.beat("vivo", now=sim.now())
    assert health.purge(now=sim.now()) == 1
    with Session(engine) as s:
        assert [h.worker_id for h in s.exec(select(WorkerHeartbeat)).all()] == ["vivo"]
