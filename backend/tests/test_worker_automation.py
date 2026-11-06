"""Automatización por política: pre-generar CV solo para `apply`, con presupuesto diario y respetando el modo de IA; nunca aplica ni envía."""
from datetime import timedelta

from sqlmodel import Session, select

from app.config import settings
from app.db import ApplicationRow, JobRow, JobTask, TailoredRow, engine
from app.scheduling import automation
from app.worker.loop import Worker
from tests.conftest import register
from tests.shared import API
from tests.test_scheduling_engine import _saved, _setup
from tests.test_worker_support import CountingSource, mk_job, onboard, set_prefs, sim  # noqa: F401  (fixture)


def _lenient(client, h):
    """Umbral de `apply` mínimo: toda oferta sin veto recomienda aplicar."""
    client.put(f"{API}/me/preferences", json={"countries": ["CO"], "target_roles": ["analista soc"], "languages": ["es"],
                                              "deal_breakers": {"apply_threshold": 1.0, "consider_threshold": 1.0}}, headers=h)


def _run_once(sim):
    sim.advance(minutes=2)
    Worker("w", clock=sim.now).run_until_idle()


def test_auto_tailor_only_for_apply_within_daily_budget_and_never_applies(client, monkeypatch, sim, llm):
    monkeypatch.setattr("app.llm.factory.build_llm", lambda role="fast", user_id=None, access=None: llm)   # el trabajador crea sus clientes con la fábrica
    h, _ = _setup(client, monkeypatch)
    _lenient(client, h)
    set_prefs(client, h, auto_tailor=True, tailor_daily_budget=2, min_score=1)
    _saved(client, h)
    _run_once(sim)
    with Session(engine) as s:
        tasks = s.exec(select(JobTask).where(JobTask.kind == "auto_tailor")).all()
        assert len(tasks) == 2 and all(t.status == "done" for t in tasks)                      # 3 ofertas, presupuesto 2
        assert len(s.exec(select(TailoredRow)).all()) == 2
        assert s.exec(select(ApplicationRow)).all() == []                                     # NUNCA aplica ni registra aplicación
        assert {j.status for j in s.exec(select(JobRow)).all()} == {"new"}
    # al día siguiente el presupuesto se renueva (las filas llevan hora real, así que se retrasan para emular el paso del tiempo)
    with Session(engine) as s:
        for t in s.exec(select(JobTask)).all():
            t.created_at = t.created_at - timedelta(days=5)
            s.add(t)
        s.commit()
    sim.advance(days=1)
    monkeypatch.setattr("app.services.runner.build_sources", lambda c=None: [CountingSource("stub", [mk_job(i) for i in range(1, 6)])])
    _run_once(sim)
    with Session(engine) as s:
        assert len(s.exec(select(TailoredRow)).all()) == 4                                    # +2 (las ofertas 4 y 5); las 2 ya adaptadas no se repiten


def test_nothing_is_pregenerated_when_the_policy_is_off_or_the_offer_is_not_apply(client, monkeypatch, sim, llm):
    monkeypatch.setattr("app.llm.factory.build_llm", lambda role="fast", user_id=None, access=None: llm)   # el trabajador crea sus clientes con la fábrica
    h, _ = _setup(client, monkeypatch)
    _saved(client, h)
    _run_once(sim)                                                                            # política por defecto: auto_tailor apagado
    with Session(engine) as s:
        assert s.exec(select(JobTask)).all() == []
    set_prefs(client, h, auto_tailor=True, min_score=1)
    client.put(f"{API}/me/preferences", json={"countries": ["CO"], "target_roles": ["analista soc"], "languages": ["es"],
                                              "deal_breakers": {"apply_threshold": 5.0, "consider_threshold": 5.0}}, headers=h)       # nada llega a `apply`
    sim.advance(hours=3)
    monkeypatch.setattr("app.services.runner.build_sources", lambda c=None: [CountingSource("stub", [mk_job(40)])])
    _run_once(sim)
    with Session(engine) as s:
        assert s.exec(select(JobTask)).all() == []


def test_without_ai_tailor_is_only_queued_if_an_extractive_mode_exists_or_operator_forces(client, monkeypatch, sim, llm):
    monkeypatch.setattr("app.llm.factory.build_llm", lambda role="fast", user_id=None, access=None: llm)   # el trabajador crea sus clientes con la fábrica
    monkeypatch.setattr(settings, "ai_mode", "none")
    monkeypatch.setattr(automation, "extractive_tailor_exists", lambda: False)                # simula un despliegue sin modo extractivo
    assert automation.tailor_possible() is False
    h, _ = _setup(client, monkeypatch)
    _lenient(client, h)
    set_prefs(client, h, auto_tailor=True, min_score=1)
    assert client.get(f"{API}/me/notifications/preferences", headers=h).json()["tailor_available"] is False
    _saved(client, h)
    _run_once(sim)
    with Session(engine) as s:
        assert s.exec(select(JobTask)).all() == []                                            # sin IA y sin extractivo: no se encola
        assert len(s.exec(select(JobRow)).all()) == 3 and s.exec(select(JobRow)).first().evaluation   # pero SÍ se evaluó (determinista)
    monkeypatch.setattr(settings, "worker_pregen_without_ai", True)                           # el operador asegura que el handler degrada
    assert automation.tailor_possible() is True
    monkeypatch.setattr(automation, "extractive_tailor_exists", lambda: True)
    monkeypatch.setattr(settings, "worker_pregen_without_ai", None)
    assert automation.tailor_possible() is True


def test_auto_tailor_is_an_internal_task_users_cannot_trigger(client, monkeypatch, llm):
    h, _ = _setup(client, monkeypatch)
    client.post(f"{API}/me/searches", json={}, headers=h)
    jid = client.get(f"{API}/me/jobs", headers=h).json()["items"][0]["id"]
    t = client.post(f"{API}/me/jobs/{jid}/tasks", json={"kind": "auto_tailor"}, headers=h).json()
    done = client.get(f"{API}/me/tasks/{t['id']}", headers=h).json()
    assert done["status"] == "failed" and "interna" in done["error"]


def test_automation_is_isolated_between_users(client, monkeypatch, sim, llm):
    monkeypatch.setattr("app.llm.factory.build_llm", lambda role="fast", user_id=None, access=None: llm)   # el trabajador crea sus clientes con la fábrica
    ha, _ = _setup(client, monkeypatch, "a@x.com")
    hb = register(client, "b@x.com")
    onboard(client, hb)
    _lenient(client, ha)
    set_prefs(client, ha, auto_tailor=True, min_score=1)
    set_prefs(client, hb, min_score=1)
    _saved(client, ha)
    _saved(client, hb)
    sim.advance(minutes=2)
    Worker("w", clock=sim.now, concurrency=1).run_until_idle()
    with Session(engine) as s:
        assert {t.user_id for t in s.exec(select(JobTask)).all()} == {1}                      # solo A pidió pre-generación
        assert {t.user_id for t in s.exec(select(TailoredRow)).all()} == {1}
    assert len(client.get(f"{API}/me/notifications", headers=hb).json()["items"]) >= 1       # B recibió su propio aviso
