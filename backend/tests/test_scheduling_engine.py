"""Motor de programación: disparo sin duplicados, ventana, backoff y pausa por fallos, caché compartida entre usuarios y presupuesto de peticiones."""
import threading
from datetime import timedelta

from sqlmodel import Session, select

from app.config import settings
from app.db import JobRow, Notification, SavedSearch, SearchRun, SourceCache, WorkItem, engine
from app.scheduling import budget, cache
from app.scheduling import service as sched
from app.worker.loop import Worker
from tests.conftest import register
from tests.shared import API
from tests.test_worker_support import CountingSource, mk_job, onboard, sim  # noqa: F401  (fixture)

URL = f"{API}/me/saved-searches"


def _setup(client, monkeypatch, email="luis@x.com", jobs=None, source=None, countries=("CO",)):
    h = register(client, email)
    onboard(client, h)
    if tuple(countries) != ("CO",):
        client.put(f"{API}/me/preferences", json={"countries": list(countries), "target_roles": ["analista soc"], "languages": ["es"]}, headers=h)
    src = source or CountingSource("stub", jobs or [mk_job(1), mk_job(2), mk_job(3)])
    monkeypatch.setattr("app.services.runner.build_sources", lambda c=None: [src])
    return h, src


def _saved(client, h, **kw):
    r = client.post(URL, json={"name": "SOC", "keywords": ["analista soc"], "interval_minutes": 60, **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _due(sim, minutes=2):
    sim.advance(minutes=minutes)


def _runs(uid=None):
    with Session(engine) as s:
        q = select(SearchRun)
        return list(s.exec(q.where(SearchRun.user_id == uid) if uid else q).all())


def test_due_search_creates_run_and_work_item_and_advances_next_run(client, monkeypatch, sim):
    h, src = _setup(client, monkeypatch)
    sid = _saved(client, h)
    assert sched.enqueue_due(sim.now()) == 0                                  # aún no vence (primer disparo con jitter de hasta ~1 min)
    _due(sim)
    assert sched.enqueue_due(sim.now()) == 1
    with Session(engine) as s:
        run = s.exec(select(SearchRun)).one()
        item = s.exec(select(WorkItem)).one()
        ss = s.get(SavedSearch, sid)
    assert run.params["saved_search_id"] == sid and run.params["scheduled"] and item.kind == "search" and item.ref_id == run.id
    assert ss.next_run_at > sim.now() + timedelta(minutes=45)                 # ~60 min +-15 % de jitter
    assert sched.enqueue_due(sim.now()) == 0                                  # no se dispara dos veces


def test_concurrent_schedulers_do_not_duplicate_a_run(client, monkeypatch, sim):
    h, _ = _setup(client, monkeypatch)
    _saved(client, h)
    _due(sim)
    results = []
    ts = [threading.Thread(target=lambda: results.append(sched.enqueue_due(sim.now()))) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(results) == 1 and len(_runs()) == 1


def test_outside_window_does_not_run_and_waits_for_the_opening(client, monkeypatch, sim):
    h, _ = _setup(client, monkeypatch)
    sid = _saved(client, h, timezone="UTC", window_start="20:00", window_end="23:00")        # son las 12:00 UTC
    with Session(engine) as s:
        ss = s.get(SavedSearch, sid)
        ss.next_run_at = sim.now() - timedelta(minutes=1)
        s.add(ss)
        s.commit()
    assert sched.enqueue_due(sim.now()) == 0 and _runs() == []
    with Session(engine) as s:
        nxt = s.get(SavedSearch, sid).next_run_at
    assert nxt.hour == 20 and nxt.date() == sim.now().date()
    sim.t = nxt + timedelta(minutes=1)
    assert sched.enqueue_due(sim.now()) == 1


def test_user_with_active_run_is_postponed_and_missing_cv_is_reported(client, monkeypatch, sim):
    h, _ = _setup(client, monkeypatch)
    _saved(client, h)
    _due(sim)
    assert sched.enqueue_due(sim.now()) == 1
    sim.advance(minutes=61)
    assert sched.enqueue_due(sim.now()) == 0                                  # la anterior sigue en cola: se pospone ~5 min
    sim.advance(minutes=6)
    Worker("w", clock=sim.now).run_until_idle()
    sim.advance(minutes=80)
    assert sched.enqueue_due(sim.now()) == 1
    h2 = register(client, "sincv@x.com")
    sid = _saved(client, h2)
    sim.advance(minutes=3)
    assert sched.enqueue_due(sim.now()) == 0
    with Session(engine) as s:
        assert "CV" in s.get(SavedSearch, sid).last_error


def test_only_new_jobs_are_reported_on_each_run(client, monkeypatch, sim):
    pool = [mk_job(1), mk_job(2)]
    h, src = _setup(client, monkeypatch, jobs=None, source=CountingSource("stub", lambda: pool))
    client.put(f"{API}/me/notifications/preferences", json={"min_score": 1}, headers=h)
    _saved(client, h)
    w = Worker("w", clock=sim.now)
    for step in range(3):
        sim.advance(minutes=75)
        w.run_until_idle()
        if step == 0:
            pool.append(mk_job(3))
    with Session(engine) as s:
        notes = s.exec(select(Notification).where(Notification.kind == "new_jobs").order_by(Notification.id)).all()
        runs = s.exec(select(SearchRun).order_by(SearchRun.id)).all()
    assert [r.new_jobs for r in runs][:3] == [2, 1, 0]
    assert [len(n.data["job_ids"]) for n in notes] == [2, 1]                  # el tercer ciclo no trae nada nuevo: no hay aviso
    ids = [i for n in notes for i in n.data["job_ids"]]
    assert len(ids) == len(set(ids)) == 3


# ------------------------------------------------------------------ fallos: backoff exponencial y pausa automática
class FailingSource(CountingSource):
    def search(self, q):
        self.calls += 1
        raise RuntimeError("portal caído")


def test_failures_back_off_exponentially_then_pause_with_notice_and_resume(client, monkeypatch, sim):
    monkeypatch.setattr(settings, "schedule_max_failures", 3)
    h, src = _setup(client, monkeypatch, source=FailingSource("stub", lambda: []))
    sid = _saved(client, h)
    w = Worker("w", clock=sim.now)
    gaps = []
    for _ in range(3):
        sim.advance(minutes=1)
        with Session(engine) as s:
            s.get(SavedSearch, sid)
        # fuerza el vencimiento y ejecuta
        with Session(engine) as s:
            ss = s.get(SavedSearch, sid)
            ss.next_run_at = sim.now() - timedelta(seconds=1)
            s.add(ss)
            s.commit()
        w.run_until_idle()
        with Session(engine) as s:
            ss = s.get(SavedSearch, sid)
            gaps.append((ss.next_run_at - sim.now()).total_seconds() / 60 if ss.active else None)
    assert gaps[0] > 90 and gaps[1] > 190                                     # 60*2^1 y 60*2^2 min (con jitter +-15 %)
    assert gaps[2] is None                                                    # a la 3.ª falla se pausa
    with Session(engine) as s:
        ss = s.get(SavedSearch, sid)
        assert not ss.active and "3 fallos" in ss.paused_reason and ss.failure_streak == 3
        n = s.exec(select(Notification).where(Notification.kind == "search_paused")).one()
        assert "SOC" in n.title
    calls = src.calls
    sim.advance(days=3)
    w.run_until_idle()
    assert src.calls == calls                                                 # pausada: no vuelve a molestar al portal
    assert client.post(f"{URL}/{sid}/resume", headers=h).json()["failure_streak"] == 0


def test_partial_source_failure_is_not_a_failure(client, monkeypatch, sim):
    h, _ = _setup(client, monkeypatch)
    sid = _saved(client, h)
    _due(sim)
    Worker("w", clock=sim.now).run_until_idle()
    with Session(engine) as s:
        assert s.get(SavedSearch, sid).failure_streak == 0 and s.get(SavedSearch, sid).total_runs >= 1


# ------------------------------------------------------------------ caché compartida entre usuarios
def test_second_user_with_same_query_does_not_hit_the_source(client, monkeypatch, sim):
    h1, src = _setup(client, monkeypatch, "a@x.com")
    h2 = register(client, "b@x.com")
    onboard(client, h2)
    _saved(client, h1)
    _saved(client, h2)
    _due(sim)
    Worker("w", clock=sim.now, concurrency=1).run_until_idle()                # dos usuarios, misma consulta y país
    assert src.calls == 1                                                      # la 2.ª salió de la caché compartida
    with Session(engine) as s:
        assert s.exec(select(SourceCache)).one().hits == 1
        by_user = {}
        for r in s.exec(select(JobRow)).all():
            by_user.setdefault(r.user_id, []).append(r.dedup_key)
    assert len(by_user) == 2 and sorted(by_user[1]) == sorted(by_user[2])      # ambos reciben sus ofertas, cada uno en SUS filas


def test_cache_key_depends_on_country_and_filters_but_not_on_order_or_accents(client, monkeypatch, sim):
    from app.domain.job import SearchQuery
    a = SearchQuery(keywords=["Analista SOC", "Ciberseguridad"], countries=["CO"])
    b = SearchQuery(keywords=["ciberseguridad", "analista  soc"], countries=["CO"])
    c = SearchQuery(keywords=["analista soc", "ciberseguridad"], countries=["MX"])
    d = SearchQuery(keywords=["analista soc", "ciberseguridad"], countries=["CO"], hours_old=24)
    assert cache.cache_key("x", a)[0] == cache.cache_key("x", b)[0]
    assert len({cache.cache_key("x", q)[0] for q in (a, c, d)}) == 3
    assert cache.cache_key("x", a)[0] != cache.cache_key("y", a)[0]
    assert "CO" in cache.cache_key("x", a)[0]


def test_different_country_is_not_shared_and_ttl_expiry_refetches(client, monkeypatch, sim):
    h1, src = _setup(client, monkeypatch, "a@x.com")
    h2 = register(client, "b@x.com")
    onboard(client, h2)
    client.put(f"{API}/me/preferences", json={"countries": ["MX"], "target_roles": ["analista soc"], "languages": ["es"]}, headers=h2)
    _saved(client, h1)
    _saved(client, h2)
    _due(sim)
    Worker("w", clock=sim.now, concurrency=1).run_until_idle()
    assert src.calls == 2
    sim.advance(seconds=settings.cache_ttl_s + 60)                             # caduca
    with Session(engine) as s:
        for ss in s.exec(select(SavedSearch)).all():
            ss.next_run_at = sim.now() - timedelta(seconds=1)
            s.add(ss)
        s.commit()
    Worker("w", clock=sim.now, concurrency=1).run_until_idle()
    assert src.calls == 4


def test_manual_searches_bypass_the_shared_cache_unless_enabled(client, monkeypatch, sim):
    h, src = _setup(client, monkeypatch)
    client.post(f"{API}/me/searches", json={}, headers=h)
    client.post(f"{API}/me/searches", json={}, headers=h)
    assert src.calls == 2 and cache.invalidate() == 0
    monkeypatch.setattr(settings, "cache_manual", True)
    client.post(f"{API}/me/searches", json={}, headers=h)
    client.post(f"{API}/me/searches", json={}, headers=h)
    assert src.calls == 3


def test_blocked_or_failed_results_are_never_shared(monkeypatch, sim):
    from app.domain.job import SearchQuery
    q = SearchQuery(keywords=["x"], countries=["CO"])
    src = CountingSource("p", [mk_job(1)])
    wrapped = cache.SharedCacheSource(src, use_cache=True)
    monkeypatch.setattr("app.scout.fetch.events", lambda: {"blocked": 2, "ok": 0, "requests": 2, "errors": 0, "last_blocked": "429"})
    wrapped.search(q)
    wrapped.search(q)
    assert src.calls == 2 and cache.invalidate() == 0                          # bloqueado: ni se guarda
    monkeypatch.setattr("app.scout.fetch.events", lambda: {"blocked": 0, "ok": 1, "requests": 1, "errors": 0, "last_blocked": ""})
    wrapped.search(q)
    wrapped.search(q)
    assert src.calls == 3
    assert cache.invalidate(source="p", country="CO") == 1 and cache.invalidate() == 0


def test_empty_results_expire_sooner_and_purge_removes_expired(monkeypatch, sim):
    from app.domain.job import SearchQuery
    monkeypatch.setattr("app.scout.fetch.events", lambda: {"blocked": 0, "ok": 1, "requests": 1, "errors": 0, "last_blocked": ""})
    q = SearchQuery(keywords=["x"], countries=["CO"])
    src = CountingSource("p", [])
    w = cache.SharedCacheSource(src, use_cache=True)
    w.search(q)
    w.search(q)
    assert src.calls == 1
    sim.advance(seconds=settings.cache_empty_ttl_s + 1)
    w.search(q)
    assert src.calls == 2
    sim.advance(seconds=settings.cache_empty_ttl_s + 1)
    assert cache.purge(sim.now()) == 1


# ------------------------------------------------------------------ presupuesto de peticiones
def test_request_budget_counts_real_requests_and_skips_exhausted_sources(client, monkeypatch, sim):
    from app.scout.ratelimit import limiter

    class Fetching(CountingSource):
        def search(self, q):
            for _ in range(3):
                limiter.acquire("https://portal.example/jobs")
            return super().search(q)

    monkeypatch.setattr(settings, "budget_domain_per_hour", 5)
    monkeypatch.setattr(settings, "budget_global_per_hour", 0)
    monkeypatch.setattr(limiter, "_sleep", lambda s: None)
    h, src = _setup(client, monkeypatch, source=Fetching("portal", [mk_job(1)]))
    for i in range(3):                                                         # 3 consultas DISTINTAS: la caché no las comparte
        sid = _saved(client, h, name=f"s{i}", keywords=[f"rol {i}"])
    w = Worker("w", clock=sim.now, concurrency=1)
    for _ in range(6):                                                         # una búsqueda a la vez por usuario: varias vueltas
        sim.advance(minutes=6)
        w.run_until_idle()
    assert budget.used("domain:portal.example", sim.now()) == 6                # 2 búsquedas x 3 peticiones; la 3.ª ya no se hizo
    assert src.calls == 2
    skipped = [r for r in _runs() if "portal" in r.sources and r.sources["portal"].get("outcome") == "budget"]
    assert len(skipped) == 1 and "agotado" in skipped[0].sources["portal"]["error"]
    sim.advance(hours=1)                                                       # nueva hora: presupuesto renovado
    assert budget.blocked_reason("portal", sim.now()) is None and sid


def test_global_budget_blocks_every_source(sim, monkeypatch):
    monkeypatch.setattr(settings, "budget_global_per_hour", 10)
    budget.record({"a.com": 6, "b.com": 4}, sim.now())
    assert "global" in budget.blocked_reason("whatever", sim.now())
    assert budget.purge(sim.now() + timedelta(hours=72)) >= 3


# ------------------------------------------------------------------ cuota manual
def test_scheduled_runs_do_not_consume_the_manual_search_quota(client, monkeypatch, sim):
    h, _ = _setup(client, monkeypatch)
    monkeypatch.setattr(settings, "quota_searches_per_day", 2)
    _saved(client, h)
    w = Worker("w", clock=sim.now)
    for _ in range(4):
        with Session(engine) as s:
            ss = s.exec(select(SavedSearch)).one()
            ss.next_run_at = sim.now() - timedelta(seconds=1)
            s.add(ss)
            s.commit()
        w.run_until_idle()
        sim.advance(minutes=10)
    assert len([r for r in _runs() if r.params.get("scheduled")]) == 4
    assert client.post(f"{API}/me/searches", json={}, headers=h).status_code == 202
    assert client.post(f"{API}/me/searches", json={}, headers=h).status_code == 202
    assert client.post(f"{API}/me/searches", json={}, headers=h).status_code == 429
