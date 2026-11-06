"""Auto-postulación: apagada por defecto, aprobación vs automática, ensayo, portales solo manuales, tope diario, idempotencia y seguimiento."""
import pytest
from sqlmodel import Session, select

from app.autoapply import service as svc
from app.autoapply.form import FormResult
from app.db import ApplicationRow, ApplyAttempt, JobRow, User, engine
from tests.shared import API


@pytest.fixture
def fake_form(monkeypatch):
    calls = []

    def fake(url, applicant, resume, *, dry_run, **kw):
        calls.append({"url": url, "dry_run": dry_run, "resume": resume, "applicant": applicant})
        return FormResult("dry_run" if dry_run else "submitted", "ok", filled=["email"], steps=["abrí"], final_url=url)
    monkeypatch.setattr("app.autoapply.service.run_form", fake)
    monkeypatch.setattr("app.scout.netguard.assert_public_url", lambda u: u)
    return calls


def _uid():
    with Session(engine) as s:
        return s.exec(select(User)).first().id


def _job(uid, title="Dev", source="lever:acme", score=4.5, rec="apply", key=None, status="new", run_id=None):
    with Session(engine) as s:
        j = JobRow(user_id=uid, dedup_key=key or title, status=status, run_id=run_id,
                   evaluation={"score": score, "recommendation": rec, "vetoes": [], "dimensions": []},
                   data={"title": title, "company": "Acme", "location": "Bogotá", "source": source, "url": "https://jobs.lever.co/acme/123", "description": "Python"})
        s.add(j)
        s.commit()
        return j.id


def _policy(client, auth, **kw):
    body = {"mode": "auto", "dry_run": False, "min_score": 3.5, "daily_cap": 5, "cv_mode": "base", **kw}
    r = client.put(f"{API}/me/auto-apply/policy", json=body, headers=auth)
    assert r.status_code == 200
    client.put(f"{API}/me/auto-apply/data", json={"phone": "+57 300 1112233", "email": "luis@x.com", "full_name": "Luis Gomez"}, headers=auth)


def _run(uid, jobs):
    with Session(engine) as s:
        rows = [s.get(JobRow, j) for j in jobs]
        return svc.on_new_jobs(s, s.get(User, uid), rows)


def test_policy_defaults_to_off_and_nothing_is_sent(client, ready, fake_form):
    pol = client.get(f"{API}/me/auto-apply/policy", headers=ready).json()
    assert pol["mode"] == "off" and pol["dry_run"] is True and pol["paused"] is False
    assert _run(_uid(), [_job(_uid())]) == {"queued": 0, "awaiting": 0, "skipped": 0} and not fake_form


def test_approval_mode_prepares_but_waits_for_your_click(client, ready, fake_form):
    _policy(client, ready, mode="approval")
    j = _job(_uid())
    assert _run(_uid(), [j])["awaiting"] == 1 and not fake_form
    att = client.get(f"{API}/me/auto-apply/attempts", headers=ready).json()[0]
    assert att["status"] == "awaiting_approval"
    ok = client.post(f"{API}/me/auto-apply/attempts/{att['id']}/approve", headers=ready)
    assert ok.status_code == 202 and len(fake_form) == 1
    done = client.get(f"{API}/me/auto-apply/attempts", headers=ready).json()[0]
    assert done["status"] == "submitted" and done["trigger"] == "approved"


def test_auto_mode_sends_with_base_cv_and_records_it_in_tracking(client, ready, fake_form):
    _policy(client, ready, mode="auto", cv_mode="base")
    j = _job(_uid())
    assert _run(_uid(), [j])["queued"] == 1 and len(fake_form) == 1
    assert fake_form[0]["dry_run"] is False and fake_form[0]["applicant"].phone == "+57 300 1112233"
    with Session(engine) as s:
        app = s.exec(select(ApplicationRow).where(ApplicationRow.job_id == j)).first()
        assert app and app.applied_at and app.tailored_id                      # quedó en el seguimiento con el CV exacto
        assert s.get(JobRow, j).status == "applied"
    assert client.get(f"{API}/me/jobs/{j}/application", headers=ready).json()["stage"] == "applied"


def test_dry_run_never_marks_as_applied(client, ready, fake_form):
    _policy(client, ready, mode="auto", dry_run=True)
    j = _job(_uid())
    _run(_uid(), [j])
    assert fake_form[0]["dry_run"] is True
    with Session(engine) as s:
        assert s.get(JobRow, j).status == "new"
        assert s.exec(select(ApplyAttempt)).first().status == "dry_run"


def test_tailored_mode_generates_a_cv_for_that_job(client, ready, fake_form):
    _policy(client, ready, mode="auto", cv_mode="tailored")
    j = _job(_uid())
    _run(_uid(), [j])
    with Session(engine) as s:
        a = s.exec(select(ApplyAttempt)).first()
        assert a.status == "submitted" and a.cv_mode == "tailored" and a.tailored_id


def test_portals_that_need_login_are_manual_only(client, ready, fake_form):
    _policy(client, ready, mode="auto")
    assert _run(_uid(), [_job(_uid(), source="linkedin")])["skipped"] == 1 and not fake_form
    pv = client.get(f"{API}/me/auto-apply/jobs/{_job(_uid(), source='linkedin', key='l2')}/preview", headers=ready).json()
    assert pv["can_run"] is False and pv["blockers"][0]["code"] == "manual_only"


def test_threshold_recommendation_pause_and_portal_override(client, ready, fake_form):
    _policy(client, ready, mode="auto", min_score=4.0)
    assert _run(_uid(), [_job(_uid(), score=3.6, key="a"), _job(_uid(), rec="consider", key="b")])["queued"] == 0
    _policy(client, ready, mode="auto", paused=True)
    assert _run(_uid(), [_job(_uid(), key="c")])["queued"] == 0
    _policy(client, ready, mode="auto", portal_modes={"lever": "off"})
    assert _run(_uid(), [_job(_uid(), key="d")])["queued"] == 0
    assert not fake_form


def test_daily_cap_and_no_double_application(client, ready, fake_form):
    _policy(client, ready, mode="auto", daily_cap=2)
    ids = [_job(_uid(), title=f"J{i}", key=f"k{i}") for i in range(4)]
    assert _run(_uid(), ids)["queued"] == 2 and len(fake_form) == 2
    assert _run(_uid(), ids)["queued"] == 0                                    # ni se repite ni supera el tope


def test_manual_apply_needs_explicit_confirmation(client, ready, fake_form):
    j = _job(_uid())                                                           # política apagada: «postula por mí» a mano sigue exigiendo confirmación y datos
    client.put(f"{API}/me/auto-apply/data", json={"phone": "+57 3", "email": "luis@x.com", "full_name": "Luis Gomez"}, headers=ready)
    assert client.post(f"{API}/me/auto-apply/jobs/{j}", json={}, headers=ready).status_code == 422
    r = client.post(f"{API}/me/auto-apply/jobs/{j}", json={"confirm": True, "dry_run": True, "cv_mode": "base"}, headers=ready)
    assert r.status_code == 202 and r.json()["status"] == "dry_run" and r.json()["trigger"] == "manual"


def test_missing_personal_data_blocks_and_explains(client, ready, fake_form):
    j = _job(_uid())                                                           # sin teléfono en perfil ni en «mis datos»
    pv = client.get(f"{API}/me/auto-apply/jobs/{j}/preview", headers=ready).json()
    assert "teléfono" in pv["missing_data"] and any(b["code"] == "missing_data" for b in pv["blockers"])
    assert client.post(f"{API}/me/auto-apply/jobs/{j}", json={"confirm": True}, headers=ready).status_code == 409


def test_reject_and_retry_and_isolation(client, ready, fake_form):
    from tests.conftest import register
    _policy(client, ready, mode="approval")
    j = _job(_uid())
    _run(_uid(), [j])
    att = client.get(f"{API}/me/auto-apply/attempts", headers=ready).json()[0]
    other = register(client, "otra@x.com")
    assert client.post(f"{API}/me/auto-apply/attempts/{att['id']}/approve", headers=other).status_code == 404
    assert client.post(f"{API}/me/auto-apply/attempts/{att['id']}/reject", headers=ready).json()["status"] == "rejected"
    assert client.post(f"{API}/me/auto-apply/attempts/{att['id']}/retry", headers=ready).status_code == 409      # un rechazo tuyo no se reintenta solo
    assert not fake_form
