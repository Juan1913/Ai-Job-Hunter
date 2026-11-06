"""Cola persistente: lease exclusivo entre trabajadores (hilos y PROCESOS), recuperación tras caída, reintentos con backoff y dead-letter."""
import os
import subprocess
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path

from sqlmodel import Session, select

from app.config import settings
from app.db import WorkItem, engine, utcnow
from app.worker import queue

ROOT = Path(__file__).resolve().parents[1]


def _enqueue(n: int, kind: str = "task", **kw) -> list[int]:
    with Session(engine) as s:
        return [queue.enqueue(s, kind, ref_id=i, payload={"i": i}, **kw).id for i in range(n)]


def test_claim_respects_priority_and_marks_lease():
    with Session(engine) as s:
        low = queue.enqueue(s, "task", ref_id=1).id
        high = queue.enqueue(s, "task", ref_id=2, priority=10).id
    first = queue.claim("w1", limit=1)[0]
    assert first.id == high and first.attempts == 1
    second = queue.claim("w1", limit=5)
    assert [c.id for c in second] == [low]
    with Session(engine) as s:
        it = s.get(WorkItem, high)
        assert it.state == "running" and it.locked_by == "w1" and it.locked_until > utcnow()
    assert queue.claim("w2", limit=5) == []                 # nada más listo


def test_future_items_wait_for_run_after():
    now = utcnow()
    with Session(engine) as s:
        queue.enqueue(s, "task", ref_id=1, run_after=now + timedelta(minutes=10))
    assert queue.claim("w", now=now) == []
    assert len(queue.claim("w", now=now + timedelta(minutes=11))) == 1


def test_dedup_key_returns_active_item():
    with Session(engine) as s:
        a = queue.enqueue(s, "search", ref_id=1, dedup_key="search:1")
        b = queue.enqueue(s, "search", ref_id=1, dedup_key="search:1")
        assert a.id == b.id


def test_two_workers_in_threads_never_take_the_same_item():
    ids = _enqueue(60)
    got: dict[str, list[int]] = {"a": [], "b": [], "c": []}

    start = threading.Barrier(len(got))          # los tres arrancan a la vez: sin esto uno puede vaciar la cola antes de que otro empiece

    def work(name):
        start.wait()
        while True:
            c = queue.claim(name, limit=1)
            if not c:
                return
            got[name].append(c[0].id)
            time.sleep(0.002)                    # cede el turno: fuerza el intercalado real entre hilos

    ts = [threading.Thread(target=work, args=(n,)) for n in got]
    [t.start() for t in ts]
    [t.join() for t in ts]
    allc = got["a"] + got["b"] + got["c"]
    assert sorted(allc) == sorted(ids)                      # todos reclamados...
    assert len(allc) == len(set(allc))                      # ...y ninguno dos veces
    assert sum(1 for v in got.values() if v) >= 2           # y el reparto fue real


_CHILD = """
import sys, json
from app.worker import queue
name = sys.argv[1]
mine = []
while True:
    c = queue.claim(name, limit=1)
    if not c:
        break
    mine.append(c[0].id)
print(json.dumps(mine))
"""


def test_two_worker_PROCESSES_never_take_the_same_item(tmp_path):
    """Evidencia multi-proceso real sobre el mismo archivo SQLite (WAL): dos procesos Python reclamando a la vez."""
    ids = _enqueue(80)
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    procs = [subprocess.Popen([sys.executable, "-c", _CHILD, n], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for n in ("p1", "p2")]
    outs = [p.communicate(timeout=120) for p in procs]
    assert all(p.returncode == 0 for p in procs), [o[1][-500:] for o in outs]
    import json
    mine = [json.loads(o[0].strip().splitlines()[-1]) for o in outs]
    allc = mine[0] + mine[1]
    assert sorted(allc) == sorted(ids) and len(allc) == len(set(allc))


def test_orphans_are_recovered_after_a_crash_and_reclaimed_by_another_worker():
    _enqueue(1)
    now = utcnow() + timedelta(seconds=1)
    c = queue.claim("w-muere", now=now, lease_s=30)[0]
    assert queue.recover_orphans(now=now + timedelta(seconds=10)) == ([], [])      # el lease sigue vigente
    requeued, dead = queue.recover_orphans(now=now + timedelta(seconds=31))
    assert requeued == [c.id] and dead == []
    again = queue.claim("w-vivo", now=now + timedelta(seconds=32))[0]
    assert again.id == c.id and again.attempts == 2
    assert queue.complete(c.id, "w-muere") is False         # el trabajador "muerto" que reaparece ya no es dueño
    assert queue.complete(again.id, "w-vivo") is True


def test_lease_extension_keeps_the_item_and_a_lost_lease_is_detected():
    _enqueue(1)
    now = utcnow() + timedelta(seconds=1)
    c = queue.claim("w1", now=now, lease_s=30)[0]
    assert queue.extend(c.id, "w1", now=now + timedelta(seconds=20), lease_s=30)
    assert queue.recover_orphans(now=now + timedelta(seconds=40)) == ([], [])      # renovado: no es huérfano
    queue.recover_orphans(now=now + timedelta(seconds=60))
    queue.claim("w2", now=now + timedelta(seconds=61))
    assert queue.extend(c.id, "w1") is False


def test_exhausted_attempts_go_to_dead_letter_when_the_lease_expires():
    with Session(engine) as s:
        queue.enqueue(s, "task", ref_id=1, max_attempts=2)
    now = utcnow() + timedelta(seconds=1)
    for i in range(2):
        queue.claim("w", now=now + timedelta(minutes=i * 10), lease_s=5)
        requeued, dead = queue.recover_orphans(now=now + timedelta(minutes=i * 10, seconds=6))
    assert len(dead) == 1 and dead[0].attempts == 2
    with Session(engine) as s:
        assert s.exec(select(WorkItem)).one().state == "dead"


def test_failure_retries_with_exponential_backoff_then_dead():
    with Session(engine) as s:
        queue.enqueue(s, "task", ref_id=1, max_attempts=3)
    now = utcnow() + timedelta(seconds=1)
    delays = []
    for attempt in range(1, 4):
        c = queue.claim("w", now=now, limit=1)
        assert c and c[0].attempts == attempt
        outcome = queue.fail(c[0], "w", "boom", now=now, rng=lambda: 0.5)         # jitter neutro (x1.0)
        with Session(engine) as s:
            it = s.exec(select(WorkItem)).one()
        if outcome == "queued":
            delays.append((it.run_after - now).total_seconds())
            now = it.run_after
        else:
            assert outcome == "dead" and it.state == "dead" and it.last_error == "boom"
    base = settings.worker_backoff_s
    assert delays == [base, base * 2]


def test_non_retryable_failure_goes_straight_to_dead_and_defer_does_not_burn_attempts():
    _enqueue(2)
    a, b = queue.claim("w", limit=2)
    assert queue.fail(a, "w", "no reintentable", retry=False) == "dead"
    assert queue.defer(b, "w", 600, "límite por hora")
    with Session(engine) as s:
        it = s.get(WorkItem, b.id)
        assert it.state == "queued" and it.attempts == 0 and it.run_after > utcnow() + timedelta(minutes=9)


def test_release_returns_the_item_without_spending_an_attempt():
    _enqueue(1)
    c = queue.claim("w")[0]
    assert queue.release(c.id, "w")
    again = queue.claim("w2")[0]
    assert again.id == c.id and again.attempts == 1


def test_purge_keeps_recent_and_dead_longer():
    now = utcnow()
    with Session(engine) as s:
        for st, age in (("done", 20), ("done", 1), ("dead", 20), ("dead", 40)):
            it = queue.enqueue(s, "task", ref_id=age)
            it.state, it.finished_at = st, now - timedelta(days=age)
            s.add(it)
        s.commit()
    assert queue.purge(now=now, days=14) == 2               # done de 20 d y dead de 40 d
