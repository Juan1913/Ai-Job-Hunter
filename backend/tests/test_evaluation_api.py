from sqlmodel import Session

from app.db import JobRow, engine
from app.domain.job import Job
from tests.conftest import register
from tests.shared import API


def _search(client, auth):
    assert client.post(f"{API}/me/searches", json={}, headers=auth).status_code == 202
    return client.get(f"{API}/me/jobs", headers=auth).json()["items"]


def test_evaluation_endpoint_computes_saves_and_caches(client, ready):
    items = _search(client, ready)
    jid = items[0]["id"]
    assert items[0]["evaluation"] is None
    r = client.get(f"{API}/me/jobs/{jid}/evaluation", headers=ready)
    assert r.status_code == 200
    e = r.json()
    assert 1 <= e["score"] <= 5 and e["recommendation"] in ("apply", "consider", "skip") and len(e["dimensions"]) == 6 and e["cached"] is False
    assert e["reasons_top3"] and e["weights_explained"]
    again = client.get(f"{API}/me/jobs/{jid}/evaluation", headers=ready).json()
    assert again["cached"] is True and again["fingerprint"] == e["fingerprint"]
    assert client.get(f"{API}/me/jobs/{jid}/evaluation?force=true", headers=ready).json()["cached"] is False
    assert client.get(f"{API}/me/jobs/{jid}", headers=ready).json()["evaluation"]["score"] == e["score"]
    with Session(engine) as s:
        assert s.get(JobRow, jid).evaluation["recommendation"] == e["recommendation"]
    en = client.get(f"{API}/me/jobs/{jid}/evaluation?lang=en", headers=ready).json()
    assert en["lang"] == "en" and "Skills" in en["dimensions"][0]["label"]


def test_evaluation_isolation_and_auth(client, ready):
    jid = _search(client, ready)[0]["id"]
    assert client.get(f"{API}/me/jobs/{jid}/evaluation").status_code == 401
    other = register(client, "otro@x.com")
    assert client.get(f"{API}/me/jobs/{jid}/evaluation", headers=other).status_code == 404


def test_deal_breakers_in_preferences_change_the_report_and_invalidate_cache(client, ready):
    jid = _search(client, ready)[0]["id"]
    first = client.get(f"{API}/me/jobs/{jid}/evaluation", headers=ready).json()
    prefs = {"countries": ["CO"], "target_roles": ["analista soc"], "languages": ["es"], "deal_breakers": {"excluded_companies": ["Bancolombia"]}}
    assert client.put(f"{API}/me/preferences", json=prefs, headers=ready).status_code == 200
    e = client.get(f"{API}/me/jobs/{jid}/evaluation", headers=ready).json()
    assert e["cached"] is False and e["recommendation"] == "skip" and e["vetoes"][0]["code"] == "excluded_company" and e["fingerprint"] != first["fingerprint"]


def test_list_filter_by_recommendation_and_sort_by_score(client, ready):
    items = _search(client, ready)
    assert len(items) == 2
    page = client.get(f"{API}/me/jobs?sort=score", headers=ready).json()
    scores = [i["evaluation"]["score"] for i in page["items"]]
    assert scores == sorted(scores, reverse=True) and all(i["evaluation"] for i in page["items"])
    recs = {i["evaluation"]["recommendation"] for i in page["items"]}
    for rec in recs:
        got = client.get(f"{API}/me/jobs?recommendation={rec}", headers=ready).json()
        assert got["total"] >= 1 and all(i["evaluation"]["recommendation"] == rec for i in got["items"])
    assert client.get(f"{API}/me/jobs?recommendation=nada", headers=ready).status_code == 422


def test_batch_task_evaluates_everything(client, ready):
    _search(client, ready)
    r = client.post(f"{API}/me/evaluations", headers=ready)
    assert r.status_code == 202
    t = client.get(f"{API}/me/tasks/{r.json()['id']}", headers=ready).json()
    assert t["status"] == "done" and t["result"]["evaluated"] == 2
    assert sum(t["result"][k] for k in ("apply", "consider", "skip")) == 2
    single = _search(client, ready)[0]["id"]
    r2 = client.post(f"{API}/me/jobs/{single}/tasks", json={"kind": "evaluate"}, headers=ready)
    assert r2.status_code == 202 and client.get(f"{API}/me/tasks/{r2.json()['id']}", headers=ready).json()["result"]["recommendation"]


def test_llm_requirement_extraction_is_optional_and_cached(client, ready, monkeypatch):
    """Descripción larga sin años/nivel/idiomas que las regex vean: el task usa UNA llamada y no la repite."""
    from app.evaluation import service
    from app.llm.client import LLMError

    class CountingLLM:
        def __init__(self, fail=False):
            self.calls, self.fail = 0, fail

        def complete_json(self, system, user):
            self.calls += 1
            assert "<<<OFERTA_NO_CONFIABLE>>>" in user
            if self.fail:
                raise LLMError("caído")
            return {"years": 4, "level": "senior", "languages": [{"code": "en", "level": 5, "required": True}]}

    long_desc = ("Nuestra empresa busca un perfil técnico para monitorear plataformas críticas y responder a eventos. " * 8)
    _search(client, ready)
    with Session(engine) as s:
        row = s.get(JobRow, 1)
        j = Job.model_validate(row.data)
        j.description = long_desc
        row.data = j.model_dump()
        s.add(row)
        s.commit()
        from app.api.deps import load_profile
        from app.db import User
        profile = load_profile(s, s.get(User, row.user_id))
        down = CountingLLM(fail=True)
        e0 = service.evaluate_row(s, row, profile, llm=down)
        assert down.calls == 1 and e0.requirements.source == "regex"              # LLM caído: se degrada a regex sin romper
        llm = CountingLLM()
        e1 = service.evaluate_row(s, row, profile, llm=llm)
        assert llm.calls == 1 and e1.requirements.source == "llm" and e1.requirements.years == 4
        e2 = service.evaluate_row(s, row, profile, llm=llm, force=True)
        assert llm.calls == 1 and e2.requirements.years == 4                    # caché del informe: sin nueva llamada
