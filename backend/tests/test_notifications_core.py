"""Avisos en-app: dedup, umbral, silencio nocturno, resumen diario/semanal, lectura y aislamiento entre usuarios."""
from datetime import timedelta

from sqlmodel import Session, select

from app.config import settings
from app.db import Notification, NotificationChannel, WorkItem, engine
from app.notifications import channels
from app.notifications import service as svc
from app.worker.loop import Worker
from tests.conftest import register
from tests.shared import API
from tests.test_scheduling_engine import _saved, _setup
from tests.test_worker_support import CountingSource, mk_job, set_prefs, sim  # noqa: F401  (fixture)

N = f"{API}/me/notifications"


def _uid(client, h):
    return client.get(f"{API}/me", headers=h).json()["id"]


def _make(client, h, kind="new_jobs", **kw):
    with Session(engine) as s:
        return svc.create(s, _uid(client, h), kind, kw.pop("title", "t"), "b", **kw)


def test_create_dedups_and_read_flow(client, auth):
    n1 = _make(client, auth, dedup_key="run:1")
    assert n1 is not None and _make(client, auth, dedup_key="run:1") is None          # el mismo hecho no se avisa dos veces
    _make(client, auth, title="otro")
    r = client.get(N, headers=auth).json()
    assert r["unread"] == 2 and [i["title"] for i in r["items"]] == ["otro", "t"]
    assert client.post(f"{N}/{n1.id}/read", headers=auth).json()["read"] is True
    assert client.get(f"{N}/unread-count", headers=auth).json() == {"unread": 1}
    assert [i["id"] for i in client.get(N, params={"unread": True}, headers=auth).json()["items"]] != [n1.id]
    assert client.post(f"{N}/read-all", headers=auth).json() == {"marked": 1}
    assert client.get(f"{N}/unread-count", headers=auth).json() == {"unread": 0}


def test_notifications_are_isolated_between_users(client, auth):
    n = _make(client, auth)
    other = register(client, "otra@x.com")
    assert client.get(N, headers=other).json() == {"unread": 0, "items": []}
    assert client.post(f"{N}/{n.id}/read", headers=other).status_code == 404
    assert client.get(f"{N}/unread-count", headers=other).json()["unread"] == 0
    ch = client.post(f"{N}/channels", json={"kind": "webhook", "url": "http://8.8.8.8/h", "secret": "s"}, headers=auth).json()
    assert client.get(f"{N}/channels", headers=other).json()["items"] == []
    assert client.patch(f"{N}/channels/{ch['id']}", json={"label": "x"}, headers=other).status_code == 404
    assert client.delete(f"{N}/channels/{ch['id']}", headers=other).status_code == 404
    assert client.post(f"{N}/channels/{ch['id']}/test", headers=other).status_code == 404
    assert client.get(f"{N}/deliveries", headers=other).json() == []
    assert client.get(f"{N}/preferences", headers=other).json()["timezone"] == "UTC"


def test_preferences_validation_and_defaults(client, auth):
    p = client.get(f"{N}/preferences", headers=auth).json()
    assert p["auto_evaluate"] is True and p["auto_tailor"] is False and p["digest"] == "off" and p["min_score"] == 3.0
    assert client.put(f"{N}/preferences", json={"timezone": "Marte/X"}, headers=auth).status_code == 422
    assert client.put(f"{N}/preferences", json={"quiet_start": "22:00"}, headers=auth).status_code == 422       # a medias
    assert client.put(f"{N}/preferences", json={"quiet_start": "99:00", "quiet_end": "07:00"}, headers=auth).status_code == 422
    assert client.put(f"{N}/preferences", json={"digest": "hourly"}, headers=auth).status_code == 422
    ok = set_prefs(client, auth, timezone="America/Bogota", quiet_start="22:00", quiet_end="07:00", digest="daily", min_score=3.5)
    assert ok["quiet_end"] == "07:00" and ok["digest"] == "daily"
    assert set_prefs(client, auth, quiet_start=None, quiet_end=None)["quiet_start"] is None


def test_threshold_filters_what_is_notified(client, monkeypatch, sim):
    h, _ = _setup(client, monkeypatch)
    set_prefs(client, h, min_score=5.0)                                              # nada llega a 5
    _saved(client, h)
    sim.advance(minutes=2)
    Worker("w", clock=sim.now).run_until_idle()
    with Session(engine) as s:
        assert s.exec(select(Notification).where(Notification.kind == "new_jobs")).all() == []
    set_prefs(client, h, min_score=1.0)
    sim.advance(minutes=200)
    pool = [mk_job(9)]
    monkeypatch.setattr("app.services.runner.build_sources", lambda c=None: [CountingSource("stub", pool)])
    Worker("w", clock=sim.now).run_until_idle()
    with Session(engine) as s:
        n = s.exec(select(Notification).where(Notification.kind == "new_jobs")).one()
        assert len(n.data["job_ids"]) == 1 and n.data["jobs"][0]["score"] >= 1


def test_automatic_evaluation_is_deterministic_and_optional(client, monkeypatch, sim):
    from app.db import JobRow
    h, _ = _setup(client, monkeypatch)
    set_prefs(client, h, auto_evaluate=False)
    _saved(client, h)
    sim.advance(minutes=2)
    Worker("w", clock=sim.now).run_until_idle()
    with Session(engine) as s:
        assert all(r.evaluation is None for r in s.exec(select(JobRow)).all())
    set_prefs(client, h, auto_evaluate=True)
    sim.advance(minutes=200)
    monkeypatch.setattr("app.services.runner.build_sources", lambda c=None: [CountingSource("stub", [mk_job(7)])])
    Worker("w", clock=sim.now).run_until_idle()
    with Session(engine) as s:
        rows = {r.dedup_key: r for r in s.exec(select(JobRow)).all()}
        evaluated = [r for r in rows.values() if r.evaluation]
        assert len(evaluated) == 1 and 1 <= evaluated[0].evaluation["score"] <= 5


def test_manual_search_is_untouched_unless_the_policy_says_so(client, monkeypatch, sim):
    h, _ = _setup(client, monkeypatch)
    set_prefs(client, h, min_score=1)
    client.post(f"{API}/me/searches", json={}, headers=h)
    assert client.get(N, headers=h).json()["items"] == []
    set_prefs(client, h, apply_to_manual=True)
    monkeypatch.setattr("app.services.runner.build_sources", lambda c=None: [CountingSource("stub", [mk_job(20)])])
    client.post(f"{API}/me/searches", json={}, headers=h)
    assert len(client.get(N, headers=h).json()["items"]) == 1


# ------------------------------------------------------------------ resumen diario / semanal
def _cycle_days(client, monkeypatch, sim, h, days, per_day_new=2, step_min=30):
    """Reloj simulado: cada 30 min el trabajador programa/ejecuta; la fuente trae `per_day_new` ofertas nuevas por día."""
    counter = {"n": 100}
    pool: list = []
    monkeypatch.setattr("app.services.runner.build_sources", lambda c=None: [CountingSource("stub", lambda: list(pool))])
    w = Worker("w", clock=sim.now)
    last_day = None
    for _ in range(int(days * 24 * 60 / step_min)):
        sim.advance(minutes=step_min)
        if sim.now().date() != last_day:
            last_day = sim.now().date()
            for _ in range(per_day_new):
                counter["n"] += 1
                pool.append(mk_job(counter["n"]))
        w.run_until_idle()
    return w


def test_daily_digest_replaces_per_run_notices_and_never_duplicates(client, monkeypatch, sim):
    monkeypatch.setattr(settings, "cache_ttl_s", 60)
    h, _ = _setup(client, monkeypatch)
    set_prefs(client, h, digest="daily", digest_hour=8, timezone="America/Bogota", min_score=1)
    _saved(client, h, interval_minutes=180)
    _cycle_days(client, monkeypatch, sim, h, days=4)
    with Session(engine) as s:
        notes = s.exec(select(Notification).order_by(Notification.id)).all()
    assert {n.kind for n in notes} == {"digest"}                                       # ni un aviso por ejecución
    keys = [n.dedup_key for n in notes]
    assert len(keys) == len(set(keys)) and len(keys) in (3, 4)                         # 1 por día local, con 8 ofertas nuevas en total
    ids = [i for n in notes for i in n.data["job_ids"]]
    assert len(ids) == len(set(ids))                                                   # cada oferta, en un solo resumen
    assert all(n.title.startswith("Resumen diario") for n in notes)


def test_weekly_digest_goes_out_once_per_week(client, monkeypatch, sim):
    monkeypatch.setattr(settings, "cache_ttl_s", 60)
    h, _ = _setup(client, monkeypatch)
    set_prefs(client, h, digest="weekly", digest_weekday=0, digest_hour=8, timezone="UTC", min_score=1)
    _saved(client, h, interval_minutes=360)
    _cycle_days(client, monkeypatch, sim, h, days=16, per_day_new=1, step_min=60)       # arranca lunes 12:00 UTC (ya pasó la hora del lunes)
    with Session(engine) as s:
        notes = s.exec(select(Notification).order_by(Notification.id)).all()
    assert [n.title.split(":")[0] for n in notes] == ["Resumen semanal"] * len(notes) and 2 <= len(notes) <= 3
    assert len({n.dedup_key for n in notes}) == len(notes)
    ids = [i for n in notes for i in n.data["job_ids"]]
    assert len(ids) == len(set(ids))


def test_digest_with_nothing_pending_creates_nothing_and_discarded_jobs_are_skipped(client, monkeypatch, sim):
    from app.db import JobRow, NotificationPref, PendingAlert
    h, _ = _setup(client, monkeypatch)
    set_prefs(client, h, digest="daily", digest_hour=8, timezone="UTC")
    sim.advance(days=1)
    assert svc.flush_digests(sim.now()) == 0                                           # nada pendiente
    uid = _uid(client, h)
    with Session(engine) as s:
        row = JobRow(user_id=uid, dedup_key="k", data=mk_job(1).model_dump(), status="discarded")
        s.add(row)
        s.commit()
        s.add(PendingAlert(user_id=uid, job_id=row.id, score=4.0))
        p = s.get(NotificationPref, uid)
        p.last_digest_key = None
        s.add(p)
        s.commit()
    assert svc.flush_digests(sim.now()) == 0                                           # lo descartado no se avisa
    with Session(engine) as s:
        assert s.exec(select(PendingAlert)).all() == []


# ------------------------------------------------------------------ silencio nocturno
def test_quiet_hours_delay_outbound_channels_but_not_the_in_app_notice(client, auth, monkeypatch, sim):
    monkeypatch.setattr(channels, "assert_public_url", lambda u: u)
    sent = []
    monkeypatch.setattr(channels, "http_post", lambda url, content, headers, timeout: (sent.append(sim.now()) or (200, "ok")))
    set_prefs(client, auth, timezone="America/Bogota", quiet_start="22:00", quiet_end="07:00")
    client.post(f"{N}/channels", json={"kind": "webhook", "url": "https://hook.example/x", "secret": "s"}, headers=auth)
    sim.t = sim.t.replace(hour=3, minute=30) + timedelta(days=1)                       # 22:30 en Bogotá
    n = _make(client, auth)
    assert client.get(N, headers=auth).json()["items"][0]["id"] == n.id                # el aviso en-app existe al instante
    w = Worker("w", clock=sim.now)
    w.run_until_idle()
    assert sent == []                                                                  # en silencio: nada sale
    with Session(engine) as s:
        it = s.exec(select(WorkItem).where(WorkItem.kind == "deliver")).one()
        assert it.run_after.hour == 12 and it.run_after.minute == 0                    # 07:00 Bogotá = 12:00 UTC
    sim.t = sim.t.replace(hour=12, minute=1)
    w.run_until_idle()
    assert len(sent) == 1
    n2 = _make(client, auth, title="de día")                                           # fuera del silencio: sale enseguida
    w.run_until_idle()
    assert len(sent) == 2 and n2


def test_channel_secrets_are_not_in_account_export(client, auth):
    client.post(f"{N}/channels", json={"kind": "telegram", "chat_id": "12345", "secret": "123:ABC"}, headers=auth)
    body = client.get(f"{API}/me/export", headers=auth).text
    assert "123:ABC" not in body and "secret_enc" not in body
    with Session(engine) as s:
        assert s.exec(select(NotificationChannel)).one().secret_enc.startswith("v1.")
