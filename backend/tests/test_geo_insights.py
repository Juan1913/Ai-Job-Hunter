"""Mapa de ofertas (gazetario embebido) y análisis de candidaturas (hipótesis con evidencia, nunca veredictos)."""
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.db import ApplicationRow, JobRow, User, engine
from app.geo.gazetteer import locate
from tests.shared import API

NOW = datetime.now(timezone.utc)


def _uid():
    with Session(engine) as s:
        return s.exec(select(User)).first().id


def _job(s, uid, title, loc="Bogotá", score=None, gaps=None, remote=False, status="new", dims=None, country="CO", key=None):
    ev = None if score is None else {"score": score, "recommendation": "apply" if score >= 3.8 else "skip", "dimensions": dims or []}
    j = JobRow(user_id=uid, dedup_key=key or title, status=status, evaluation=ev, gaps=gaps, first_seen=NOW - timedelta(days=3),
               data={"title": title, "company": "Acme", "location": loc, "remote": remote, "source": "linkedin", "country": country, "url": "u"})
    s.add(j)
    s.flush()
    return j


def test_locate_prefers_city_then_country_and_respects_the_offer_country():
    assert locate("Medellín, Antioquia, Colombia")["place"] == "Medellín"
    assert locate("Colombia")["precision"] == "country"
    assert locate("Remoto") is None
    assert locate("San José", "US")["precision"] == "country"        # «San José» ambiguo: manda el país de la oferta
    assert locate("", "CO")["place"] == "Colombia"
    assert locate("Springfield") is None                             # lo desconocido no se inventa


def test_map_groups_by_place_and_counts_remote_and_unlocated(client, auth):
    uid = _uid()
    with Session(engine) as s:
        _job(s, uid, "A", "Bogotá", 4.2)
        _job(s, uid, "B", "Bogotá, Colombia", 3.1)
        _job(s, uid, "C", "Medellín", 2.0)
        _job(s, uid, "D", "Remoto", remote=True, country=None)
        _job(s, uid, "E", "Narnia", country=None)
        _job(s, uid, "F", "Bogotá", status="discarded")
        s.commit()
    r = client.get(f"{API}/me/map/jobs", headers=auth).json()
    assert r["total"] == 5 and r["remote"] == 1 and r["unlocated"] == 1 and r["located"] == 3
    top = r["points"][0]
    assert top["place"] == "Bogotá" and top["count"] == 2 and top["best_score"] == 4.2 and [j["title"] for j in top["jobs"]] == ["A", "B"]


def test_map_is_isolated_per_user(client, auth):
    from tests.conftest import register
    with Session(engine) as s:
        _job(s, _uid(), "Mía", "Bogotá")
        s.commit()
    other = register(client, "otra@x.com")
    assert client.get(f"{API}/me/map/jobs", headers=other).json()["total"] == 0


def _apply(s, uid, job, outcome=None, days_ago=10, reason=None, interviews=None, stage="applied"):
    a = ApplicationRow(user_id=uid, job_id=job.id, stage=stage, applied_at=NOW - timedelta(days=days_ago), outcome=outcome, outcome_reason=reason,
                       outcome_at=NOW if outcome else None, interviews=interviews or [])
    s.add(a)
    return a


def test_insights_without_applications_asks_for_data(client, auth):
    r = client.get(f"{API}/me/pipeline/insights", headers=auth).json()
    assert r["sample"]["sent"] == 0 and r["actions"] == [] and "Aún no" in r["headline"] and r["mode"] == "rules"


def test_insights_finds_recurring_gap_low_fit_and_missing_cv(client, auth):
    uid = _uid()
    gap = [{"keyword": "Kubernetes", "status": "missing", "confirmed": False}]
    with Session(engine) as s:
        for i in range(3):
            _apply(s, uid, _job(s, uid, f"Dev{i}", score=2.4, gaps=gap, key=f"k{i}"), outcome="rejected")
        s.commit()
    r = client.get(f"{API}/me/pipeline/insights", headers=auth).json()
    ids = {a["id"] for a in r["actions"]}
    assert {"skill:kubernetes", "targeting:low-fit", "cv:generic"} <= ids
    d = r["diagnoses"][0]
    codes = {c["code"] for c in d["causes"]}
    assert {"weak_fit", "skills", "no_tailored_cv"} <= codes
    assert all(c["evidence"] for c in d["causes"])                       # cada hipótesis trae su evidencia
    assert "hipótesis" in r["caveat"].lower() and r["sample"]["enough"] is True


def test_user_reason_outweighs_inferences_and_interview_is_recognised(client, auth):
    uid = _uid()
    with Session(engine) as s:
        _apply(s, uid, _job(s, uid, "X", score=4.5, key="x"), outcome="rejected", reason="Buscaban más años de experiencia")
        _apply(s, uid, _job(s, uid, "Y", score=4.5, key="y"), outcome="rejected", interviews=[{"id": "1", "done": True}])
        s.commit()
    by = {d["title"]: d for d in client.get(f"{API}/me/pipeline/insights", headers=auth).json()["diagnoses"]}
    assert by["X"]["causes"][0]["code"] == "seniority" and by["X"]["causes"][0]["confidence"] == "high"
    assert by["Y"]["causes"][0]["code"] == "after_interview"


def test_insights_flags_followups_and_lists_what_is_still_to_apply(client, auth):
    uid = _uid()
    with Session(engine) as s:
        _apply(s, uid, _job(s, uid, "Espera", key="e"), days_ago=12)
        s.add(ApplicationRow(user_id=uid, job_id=_job(s, uid, "Pendiente", score=4.4, key="p").id, stage="saved"))
        s.commit()
    r = client.get(f"{API}/me/pipeline/insights", headers=auth).json()
    assert any(a["id"] == "followup" for a in r["actions"])
    assert [t["title"] for t in r["to_apply"]] == ["Pendiente"]


def test_narrate_uses_ai_only_from_facts_and_falls_back_without_it(client, auth, llm, monkeypatch):
    uid = _uid()
    with Session(engine) as s:
        _apply(s, uid, _job(s, uid, "Z", score=2.0, key="z"), outcome="rejected")
        s.commit()
    monkeypatch.setattr("app.ai.report.usable", lambda m: None)           # modo sin IA
    r = client.get(f"{API}/me/pipeline/insights?narrate=true", headers=auth).json()
    assert r["narrative"] is None and r["mode"] == "rules" and r["actions"]
    seen = {}
    class Fake:
        def complete(self, system, user, **kw):
            seen["user"] = user
            return "Nota breve."
    monkeypatch.setattr("app.ai.report.usable", lambda m: Fake())
    r = client.get(f"{API}/me/pipeline/insights?narrate=true", headers=auth).json()
    assert r["narrative"] == "Nota breve." and r["mode"] == "llm" and "Z" in seen["user"]


def _job2(s, uid, title, key, **data):
    base = {"title": title, "company": "Acme", "location": "Bogotá", "source": "linkedin", "url": "u", "description": f"Trabajo de {title} con Python", "country": "CO"}
    j = JobRow(user_id=uid, dedup_key=key, first_seen=NOW - timedelta(days=data.pop("age", 30)), data={**base, **data})
    s.add(j)
    s.flush()
    return j.id


def test_job_filters_modality_salary_recency_role_and_sorting(client, ready):
    uid = _uid()
    with Session(engine) as s:
        a = _job2(s, uid, "Backend Python", "a", remote=True, work_mode="remote", salary_annual_min=60e6, salary_annual_max=90e6, salary_currency="COP", employment_type="full_time", age=2)
        b = _job2(s, uid, "Analista SOC", "b", work_mode="onsite", salary_annual_min=30e6, salary_annual_max=40e6, salary_currency="COP", employment_type="contract", age=20)
        c = _job2(s, uid, "Diseñador", "c", work_mode="hybrid", country="MX", source="lever:acme", age=5)
        s.commit()

    def ids(**q):
        return [i["id"] for i in client.get(f"{API}/me/jobs", params=q, headers=ready).json()["items"]]
    assert ids(work_mode="remote") == [a] and ids(work_mode="onsite") == [b] and ids(work_mode="hybrid") == [c]
    assert ids(employment_type="contract") == [b]
    assert ids(min_salary=50e6) == [a] and set(ids(with_salary="true")) == {a, b}      # sin salario publicado no entra cuando se filtra por salario
    assert set(ids(posted_days=7)) == {a, c} and ids(country="MX") == [c] and ids(source="lever") == [c]
    assert ids(q="python") == [a, b, c] or set(ids(q="python")) == {a, b, c}           # «especialidad»: también busca en la descripción
    assert ids(q="soc") == [b] and ids(q="backend python") == [a]
    assert ids(sort="recent") == [a, c, b]                                              # más reciente primero
    assert ids(sort="salary", with_salary="true") == [a, b]
    assert client.get(f"{API}/me/jobs", params={"work_mode": "volando"}, headers=ready).status_code == 422


def test_search_accepts_filters_and_a_role_independent_of_the_cv(client, ready, monkeypatch):
    seen = {}

    def fake_start(session, user_id, params):
        seen.update(params)
        from app.db import SearchRun
        r = SearchRun(user_id=user_id, params=params)
        session.add(r)
        session.commit()
        session.refresh(r)
        return r
    monkeypatch.setattr("app.api.searches.start_search", fake_start)
    r = client.post(f"{API}/me/searches", json={"keywords": ["chef"], "work_modes": ["remote", "hybrid"], "countries": ["MX"], "hours_old": 72, "employment_types": ["full_time"]}, headers=ready)
    assert r.status_code == 202 and r.json()["keywords"] == ["chef"]
    assert seen["keywords"] == ["chef"] and seen["work_modes"] == ["remote", "hybrid"] and seen["countries"] == ["MX"] and seen["hours_old"] == 72
    assert client.post(f"{API}/me/searches", json={"keywords": ["x"], "work_modes": ["nube"]}, headers=ready).status_code == 422


def test_worker_status_reports_whether_scheduled_searches_can_run(client, auth):
    from app.worker import health
    assert client.get(f"{API}/me/saved-searches/worker", headers=auth).json() == {"alive": False, "count": 0}
    health.beat("test-worker")
    assert client.get(f"{API}/me/saved-searches/worker", headers=auth).json() == {"alive": True, "count": 1}
