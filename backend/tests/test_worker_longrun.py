"""Larga duración simulada (reloj inyectable): N días de ciclos con dos trabajadores, una caída a mitad y dos usuarios con políticas distintas.
Debe terminar sin duplicar ofertas, avisos ni entregas, sin trabajos atascados y compartiendo la caché entre usuarios."""
from collections import Counter
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlmodel import Session, func, select

from app.config import settings
from app.db import JobRow, Notification, NotificationDelivery, SavedSearch, SearchRun, WorkItem, engine
from app.notifications import channels
from app.worker import queue
from app.worker.loop import Worker
from tests.conftest import register
from tests.test_notifications_core import N
from tests.test_scheduling_engine import _saved
from tests.test_worker_support import CountingSource, mk_job, onboard, set_prefs, sim  # noqa: F401  (fixture)

DAYS = 10
PER_DAY = 3
BOGOTA = ZoneInfo("America/Bogota")


def test_n_days_of_cycles_without_duplicates(client, monkeypatch, sim):
    monkeypatch.setattr(settings, "cache_ttl_s", 3600)
    monkeypatch.setattr(channels, "assert_public_url", lambda u: u)
    pushes = []
    monkeypatch.setattr(channels, "http_post", lambda url, content, headers, timeout: (pushes.append((url, sim.now())) or (200, "ok")))

    # el portal publica PER_DAY ofertas por día y solo muestra las de los últimos 4 días (las viejas desaparecen; no deben reaparecer como nuevas)
    published: list[tuple[int, object]] = []
    src = CountingSource("portal", lambda: [mk_job(i) for i, t in published if sim.now() - t < timedelta(days=4)])
    monkeypatch.setattr("app.services.runner.build_sources", lambda c=None: [src])

    h1, h2 = register(client, "inmediato@x.com"), register(client, "resumen@x.com")
    for h in (h1, h2):
        onboard(client, h)
    set_prefs(client, h1, min_score=1, timezone="America/Bogota", quiet_start="22:00", quiet_end="07:00")
    set_prefs(client, h2, min_score=1, digest="daily", digest_hour=8, timezone="America/Bogota")
    assert client.post(f"{N}/channels", json={"kind": "webhook", "url": "https://hook.example/u1", "secret": "k"}, headers=h1).status_code == 201
    _saved(client, h1, interval_minutes=180)
    _saved(client, h2, interval_minutes=240)

    w1, w2 = Worker("w1", clock=sim.now, concurrency=2), Worker("w2", clock=sim.now, concurrency=2)
    counter, last_day, crashed = 0, None, False
    for step in range(DAYS * 48):                                                  # pasos de 30 min
        sim.advance(minutes=30)
        if sim.now().date() != last_day:
            last_day = sim.now().date()
            for _ in range(PER_DAY):
                counter += 1
                published.append((counter, sim.now()))
        if step == 4 * 48 and not crashed:                                         # a mitad: w1 "muere" con un trabajo reclamado y sin completar
            crashed = True
            with Session(engine) as s:
                for ss in s.exec(select(SavedSearch)).all():
                    ss.next_run_at = sim.now() - timedelta(seconds=1)
                    s.add(ss)
                s.commit()
            from app.scheduling import service as sched
            sched.enqueue_due(sim.now())
            assert queue.claim("w1-muerto", limit=1, now=sim.now(), lease_s=60)
        for w in (w1, w2):                                                         # dos trabajadores compitiendo por la misma cola
            w.run_until_idle()

    total_jobs = len(published)
    assert total_jobs >= DAYS * PER_DAY
    with Session(engine) as s:
        # 1) ninguna oferta duplicada por usuario, y cada usuario las recibió todas (cada uno en sus propias filas)
        for uid in (1, 2):
            keys = [r.dedup_key for r in s.exec(select(JobRow).where(JobRow.user_id == uid)).all()]
            assert len(keys) == len(set(keys)) == total_jobs, (uid, len(keys), total_jobs)
        # 2) usuario 1 (aviso por ejecución): cada oferta aparece en exactamente un aviso
        n1 = s.exec(select(Notification).where(Notification.user_id == 1, Notification.kind == "new_jobs")).all()
        ids1 = [i for n in n1 for i in n.data["job_ids"]]
        assert len(ids1) == len(set(ids1)) == total_jobs and len({n.dedup_key for n in n1}) == len(n1)
        # 3) usuario 2 (resumen diario): solo digests, uno por día como máximo, cada oferta una sola vez
        n2 = s.exec(select(Notification).where(Notification.user_id == 2)).all()
        assert {n.kind for n in n2} == {"digest"} and len({n.dedup_key for n in n2}) == len(n2) <= DAYS + 1
        ids2 = [i for n in n2 for i in n.data["job_ids"]]
        assert len(ids2) == len(set(ids2)) and total_jobs - len(ids2) <= PER_DAY * 2        # lo del último día aún espera a su resumen
        # 4) una sola entrega por aviso del usuario 1 y ninguna de madrugada local (silencio 22:00-07:00)
        dels = s.exec(select(NotificationDelivery)).all()
        assert Counter(d.notification_id for d in dels).most_common(1)[0][1] == 1 and {d.status for d in dels} == {"sent"}
        assert len(dels) == len(n1) == len(pushes)
        assert all(not (t.astimezone(BOGOTA).hour >= 22 or t.astimezone(BOGOTA).hour < 7) for _, t in pushes), "salió un aviso en silencio nocturno"
        # 5) cola sana: nada atascado ni muerto, y el trabajo caído se recuperó
        states = Counter(i.state for i in s.exec(select(WorkItem)).all())
        assert states.get("running", 0) == 0 and states.get("dead", 0) == 0 and states.get("queued", 0) == 0, states
        assert w1.stats["recovered"] + w2.stats["recovered"] >= 1
        # 6) búsquedas programadas sanas y caché compartida: el portal recibió muchas menos peticiones que ejecuciones
        assert all(ss.active and ss.failure_streak == 0 for ss in s.exec(select(SavedSearch)).all())
        runs = s.exec(select(func.count()).select_from(SearchRun)).one()
        assert runs >= DAYS * 6 and src.calls < runs, (src.calls, runs)
