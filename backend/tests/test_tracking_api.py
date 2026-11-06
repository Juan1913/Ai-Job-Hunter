"""Seguimiento de candidaturas: pipeline, entrevistas, resultado, archivo inmutable, recordatorios, embudo, CSV y aislamiento por usuario."""
import csv
import io
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

from app.db import ApplicationArchive, ApplicationRow, JobRow, engine
from app.tracking import service as svc
from tests.conftest import register
from tests.shared import API

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def flow(client, ready):
    """Usuario con una oferta analizada y un CV adaptado."""
    assert client.post(f"{API}/me/searches", json={}, headers=ready).status_code == 202
    jid = next(j["id"] for j in client.get(f"{API}/me/jobs", headers=ready).json()["items"] if j["job"]["title"] == "Analista SOC")
    assert client.post(f"{API}/me/jobs/{jid}/analyze", headers=ready).status_code == 200
    cv = client.post(f"{API}/me/jobs/{jid}/tailor", json={}, headers=ready)
    assert cv.status_code == 200
    return ready, jid, cv.json()["id"]


def _db_app(job_id):
    with Session(engine) as s:
        return s.exec(select(ApplicationRow).where(ApplicationRow.job_id == job_id)).first()


def test_apply_links_exact_cv_archives_offer_and_syncs_status(client, flow):
    auth, jid, cv_id = flow
    r = client.post(f"{API}/me/jobs/{jid}/application/apply", json={"cover_letter": "Estimados: me interesa.", "template": "compact", "next_step": "Llamar al reclutador"}, headers=auth)
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["stage"] == "applied" and a["tailored_id"] == cv_id and a["applied_at"] and a["template"] == "compact" and a["archive_id"]
    assert client.get(f"{API}/me/jobs/{jid}", headers=auth).json()["status"] == "applied"
    arc = client.get(f"{API}/me/archive/{a['archive_id']}", headers=auth).json()
    assert arc["job"]["title"] == "Analista SOC" and arc["cv"]["headline"] and arc["cv_text"] and arc["cover_letter"] == "Estimados: me interesa."
    # re-aplicar sin cambios no duplica el archivo
    client.post(f"{API}/me/jobs/{jid}/application/apply", json={"cover_letter": "Estimados: me interesa."}, headers=auth)
    assert len(client.get(f"{API}/me/jobs/{jid}/application/archive", headers=auth).json()) == 1


def test_archive_is_immutable_even_if_the_offer_changes_or_disappears(client, flow):
    auth, jid, _ = flow
    aid = client.post(f"{API}/me/jobs/{jid}/application/apply", json={}, headers=auth).json()["archive_id"]
    with Session(engine) as s:                                  # la oferta cambia/desaparece en la fuente
        row = s.get(JobRow, jid)
        row.data = {**row.data, "title": "OTRO TÍTULO", "closed": True}
        s.add(row)
        s.commit()
    assert client.get(f"{API}/me/archive/{aid}", headers=auth).json()["job"]["title"] == "Analista SOC"
    with Session(engine) as s:
        arc = s.get(ApplicationArchive, aid)
        arc.cv_text = "manipulado"
        s.add(arc)
        with pytest.raises(ValueError, match="inmutable"):
            s.commit()
        s.rollback()
        with pytest.raises(ValueError, match="inmutable"):
            s.delete(s.get(ApplicationArchive, aid))
            s.commit()


def test_changing_the_cv_or_letter_after_applying_adds_a_version_never_edits_one(client, flow):
    auth, jid, _ = flow
    client.post(f"{API}/me/jobs/{jid}/application/apply", json={"cover_letter": "v1"}, headers=auth)
    client.patch(f"{API}/me/jobs/{jid}/application", json={"cover_letter": "v2 corregida"}, headers=auth)
    vs = client.get(f"{API}/me/jobs/{jid}/application/archive", headers=auth).json()
    assert len(vs) == 2 and vs[0]["content_hash"] != vs[1]["content_hash"]
    assert client.get(f"{API}/me/archive/{vs[0]['id']}", headers=auth).json()["cover_letter"] == "v1"
    assert client.patch(f"{API}/me/jobs/{jid}/application", json={"tailored_id": 99999}, headers=auth).status_code == 422


def test_patch_status_applied_creates_tracking_and_archive(client, flow):
    auth, jid, cv_id = flow
    assert client.get(f"{API}/me/jobs/{jid}/application", headers=auth).status_code == 404
    client.patch(f"{API}/me/jobs/{jid}", json={"status": "applied"}, headers=auth)
    a = client.get(f"{API}/me/jobs/{jid}/application", headers=auth).json()
    assert a["stage"] == "applied" and a["tailored_id"] == cv_id
    assert len(client.get(f"{API}/me/jobs/{jid}/application/archive", headers=auth).json()) == 1
    client.patch(f"{API}/me/jobs/{jid}", json={"status": "interview"}, headers=auth)
    assert client.get(f"{API}/me/jobs/{jid}/application", headers=auth).json()["stage"] == "interviewing"


def test_dates_notes_and_salary_expectation(client, flow):
    auth, jid, _ = flow
    r = client.patch(f"{API}/me/jobs/{jid}/application", json={"deadline_at": "2026-12-01T00:00:00Z", "next_step": "Prueba técnica", "next_step_at": "2026-11-10T15:00:00Z",
                                                                 "notes": "Contacto por LinkedIn", "expected_salary_min": 5000000, "expected_salary_max": 7000000,
                                                                 "expected_salary_currency": "COP"}, headers=auth)
    a = r.json()
    assert r.status_code == 200 and a["deadline_at"].startswith("2026-12-01") and a["next_step"] == "Prueba técnica" and a["expected_salary"]["currency"] == "COP"
    assert client.patch(f"{API}/me/jobs/{jid}/application", json={"expected_salary_min": 9, "expected_salary_max": 3}, headers=auth).status_code == 422
    assert client.patch(f"{API}/me/jobs/{jid}/application", json={"stage": "inventada"}, headers=auth).status_code == 422


def test_interviews_and_contacts_lifecycle(client, flow):
    auth, jid, _ = flow
    client.post(f"{API}/me/jobs/{jid}/application/apply", json={}, headers=auth)
    it = client.post(f"{API}/me/jobs/{jid}/application/interviews", json={"kind": "technical", "scheduled_at": "2026-11-12T10:00:00Z", "interviewer": "Ana"}, headers=auth)
    assert it.status_code == 201
    iid = it.json()["id"]
    a = client.get(f"{API}/me/jobs/{jid}/application", headers=auth).json()
    assert a["stage"] == "interviewing" and a["first_response_at"] and len(a["interviews"]) == 1
    assert client.get(f"{API}/me/jobs/{jid}", headers=auth).json()["status"] == "interview"
    up = client.patch(f"{API}/me/jobs/{jid}/application/interviews/{iid}", json={"done": True, "notes": "Preguntaron por Splunk"}, headers=auth).json()
    assert up["done"] is True and up["notes"] == "Preguntaron por Splunk" and up["interviewer"] == "Ana"
    assert client.get(f"{API}/me/jobs/{jid}/application", headers=auth).json()["interviews"][0]["notes"] == "Preguntaron por Splunk"      # persistió
    assert client.post(f"{API}/me/jobs/{jid}/application/interviews", json={"kind": "raro"}, headers=auth).status_code == 422
    c = client.post(f"{API}/me/jobs/{jid}/application/contacts", json={"name": "Marta Ruiz", "role": "Recruiter", "email": "marta@b.co"}, headers=auth).json()
    assert client.patch(f"{API}/me/jobs/{jid}/application/contacts/{c['id']}", json={"phone": "+57 1"}, headers=auth).json()["phone"] == "+57 1"
    assert client.delete(f"{API}/me/jobs/{jid}/application/contacts/{c['id']}", headers=auth).status_code == 204
    assert client.delete(f"{API}/me/jobs/{jid}/application/contacts/{c['id']}", headers=auth).status_code == 404
    assert client.delete(f"{API}/me/jobs/{jid}/application/interviews/{iid}", headers=auth).status_code == 204
    assert client.patch(f"{API}/me/jobs/{jid}/application/interviews/nope", json={"done": True}, headers=auth).status_code == 404


def test_outcome_with_reason_syncs_and_can_be_reopened(client, flow):
    auth, jid, _ = flow
    client.post(f"{API}/me/jobs/{jid}/application/apply", json={}, headers=auth)
    r = client.put(f"{API}/me/jobs/{jid}/application/outcome", json={"outcome": "rejected", "reason": "Buscaban más experiencia con QRadar"}, headers=auth).json()
    assert r["stage"] == "closed" and r["outcome"] == "rejected" and r["outcome_reason"].startswith("Buscaban") and r["first_response_at"]
    assert client.get(f"{API}/me/jobs/{jid}", headers=auth).json()["status"] == "rejected"
    assert client.put(f"{API}/me/jobs/{jid}/application/outcome", json={"outcome": "meh"}, headers=auth).status_code == 422
    re = client.put(f"{API}/me/jobs/{jid}/application/outcome", json={"outcome": None}, headers=auth).json()
    assert re["outcome"] is None and re["stage"] == "applied"
    assert client.put(f"{API}/me/jobs/{jid}/application/outcome", json={"outcome": "offer"}, headers=auth).json()["stage"] == "offer"


# ---------------------------------------------------------------- recordatorios (puros, con reloj inyectado)

def _app(**kw) -> ApplicationRow:
    return ApplicationRow(user_id=1, job_id=1, stage=kw.pop("stage", "applied"), **kw)


def kinds(app, now=NOW):
    return {r["kind"] for r in svc.reminders_for(app, {"title": "T", "company": "C"}, now)}


def test_follow_up_after_seven_days_without_response_and_close_after_three_weeks():
    assert "follow_up" not in kinds(_app(applied_at=NOW - timedelta(days=6)))
    r = svc.reminders_for(_app(applied_at=NOW - timedelta(days=7)), {"title": "T", "company": "C"}, NOW)
    assert [x["kind"] for x in r] == ["follow_up"] and r[0]["severity"] == "high" and "no envía nada" in r[0]["message"]
    assert kinds(_app(applied_at=NOW - timedelta(days=22))) == {"close_suggestion"}
    assert kinds(_app(applied_at=NOW - timedelta(days=9), first_response_at=NOW - timedelta(days=3))) == set()
    assert kinds(_app(applied_at=NOW - timedelta(days=9), last_contact_at=NOW - timedelta(days=2))) == set()      # ya hiciste seguimiento
    assert kinds(_app(applied_at=NOW - timedelta(days=30), outcome="no_response", stage="closed")) == set()


def test_next_step_deadline_and_interview_reminders():
    assert kinds(_app(applied_at=NOW, next_step="Prueba", next_step_at=NOW - timedelta(hours=2))) == {"next_step_overdue"}
    assert kinds(_app(applied_at=NOW, next_step="Prueba", next_step_at=NOW + timedelta(hours=5))) == {"next_step_due"}
    assert kinds(_app(applied_at=NOW, next_step_at=NOW + timedelta(days=4))) == set()
    assert kinds(_app(stage="saved", deadline_at=NOW + timedelta(days=2))) == {"deadline_soon"}
    assert kinds(_app(stage="saved", deadline_at=NOW - timedelta(days=1))) == {"deadline_passed"}
    soon = _app(applied_at=NOW - timedelta(days=3), first_response_at=NOW - timedelta(days=2), interviews=[{"id": "a", "kind": "technical", "scheduled_at": (NOW + timedelta(hours=20)).isoformat(), "done": False}])
    assert kinds(soon) == {"interview_soon"}
    past = _app(applied_at=NOW - timedelta(days=9), first_response_at=NOW - timedelta(days=8), interviews=[{"id": "a", "kind": "hr", "scheduled_at": (NOW - timedelta(days=6)).isoformat(), "done": True}])
    assert kinds(past) == {"post_interview_follow_up"}
    assert kinds(_app(applied_at=NOW - timedelta(days=9), snoozed_until=NOW + timedelta(days=1))) == set()


def test_reminders_endpoint_and_snooze(client, flow):
    auth, jid, _ = flow
    client.post(f"{API}/me/jobs/{jid}/application/apply", json={"applied_at": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()}, headers=auth)
    rem = client.get(f"{API}/me/reminders", headers=auth).json()
    assert [r["kind"] for r in rem] == ["follow_up"] and rem[0]["job_id"] == jid and rem[0]["title"] == "Analista SOC"
    until = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    assert client.post(f"{API}/me/jobs/{jid}/application/snooze", json={"until": until}, headers=auth).status_code == 200
    assert client.get(f"{API}/me/reminders", headers=auth).json() == []


# ---------------------------------------------------------------- embudo, descriptivos y CSV

def _mk_job(s, uid, n, **data):
    j = JobRow(user_id=uid, dedup_key=f"k{n}", data={"title": f"Puesto {n}", "company": f"Emp {n}", "url": f"https://x/{n}", "source": "greenhouse:acme", **data})
    s.add(j)
    s.flush()
    return j


def test_pipeline_summary_funnel_and_descriptive_stats(client, auth):
    uid = client.get(f"{API}/me", headers=auth).json()["id"]
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        a, b, c, d = (_mk_job(s, uid, i) for i in range(4))
        s.add_all([
            ApplicationRow(user_id=uid, job_id=a.id, stage="interviewing", applied_at=now - timedelta(days=10), first_response_at=now - timedelta(days=8), template="classic",
                           interviews=[{"id": "1", "kind": "technical", "done": True}]),
            ApplicationRow(user_id=uid, job_id=b.id, stage="applied", applied_at=now - timedelta(days=10), template="compact"),
            ApplicationRow(user_id=uid, job_id=c.id, stage="closed", applied_at=now - timedelta(days=10), first_response_at=now - timedelta(days=6), outcome="rejected", template="classic"),
            ApplicationRow(user_id=uid, job_id=d.id, stage="saved")])
        s.commit()
    r = client.get(f"{API}/me/pipeline/summary", headers=auth).json()
    assert r["total_tracked"] == 4 and r["applied"] == 3 and r["by_stage"]["saved"] == 1 and r["by_outcome"]["rejected"] == 1
    assert [f["count"] for f in r["funnel"]] == [3, 2, 1, 0]
    assert r["response_rate"] == pytest.approx(0.667, abs=1e-3) and r["interview_rate"] == pytest.approx(0.333, abs=1e-3)
    assert r["avg_days_to_first_response"] == pytest.approx(3.0, abs=0.05) and r["awaiting_followup"] == 1
    tpl = {x["key"]: x for x in r["descriptive"]["by_template"]}
    assert tpl["classic"]["applications"] == 2 and tpl["classic"]["interviews"] == 1 and tpl["classic"]["small_sample"] is True
    assert r["descriptive"]["by_source"][0]["key"] == "greenhouse" and "observar" in r["caveat"]


def test_summary_keyword_correlation_is_descriptive_only(client, flow):
    auth, jid, _ = flow
    client.post(f"{API}/me/jobs/{jid}/application/apply", json={}, headers=auth)
    client.post(f"{API}/me/jobs/{jid}/application/interviews", json={"kind": "hr"}, headers=auth)
    r = client.get(f"{API}/me/pipeline/summary", headers=auth).json()
    assert r["descriptive"]["by_keyword"] == []             # con una sola candidatura no se publica ninguna correlación
    assert r["descriptive"]["by_ats_band"] and r["descriptive"]["by_ats_band"][0]["interviews"] == 1


def test_csv_export_and_formula_injection(client, flow):
    auth, jid, cv_id = flow
    client.post(f"{API}/me/jobs/{jid}/application/apply", json={"note": "=HYPERLINK(\"http://malo\")"}, headers=auth)
    r = client.get(f"{API}/me/pipeline/export.csv", headers=auth)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 1 and rows[0]["title"] == "Analista SOC" and rows[0]["stage"] == "applied" and rows[0]["tailored_cv_id"] == str(cv_id)
    assert rows[0]["archived_versions"] == "1" and rows[0]["notes"].startswith("'=")


# ---------------------------------------------------------------- aislamiento y cuenta

def test_other_users_get_404_everywhere(client, flow):
    auth, jid, _ = flow
    aid = client.post(f"{API}/me/jobs/{jid}/application/apply", json={}, headers=auth).json()["archive_id"]
    iid = client.post(f"{API}/me/jobs/{jid}/application/interviews", json={"kind": "hr"}, headers=auth).json()["id"]
    other = register(client, "otra@x.com")
    base = f"{API}/me/jobs/{jid}/application"
    checks = [("get", base, None), ("put", base, None), ("patch", base, {"notes": "x"}), ("post", base + "/apply", {}), ("put", base + "/outcome", {"outcome": "offer"}),
              ("post", base + "/interviews", {"kind": "hr"}), ("patch", f"{base}/interviews/{iid}", {"done": True}), ("delete", f"{base}/interviews/{iid}", None),
              ("post", base + "/contacts", {"name": "x"}), ("get", base + "/archive", None), ("get", f"{API}/me/archive/{aid}", None),
              ("post", base + "/snooze", {"until": "2030-01-01T00:00:00Z"}), ("post", f"{API}/me/jobs/{jid}/prep", {}), ("get", f"{API}/me/jobs/{jid}/prep", None)]
    for method, url, body in checks:
        r = getattr(client, method)(url, headers=other, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (method, url, r.status_code)
    assert client.get(f"{API}/me/applications", headers=other).json() == [] and client.get(f"{API}/me/archive", headers=other).json() == []
    assert client.get(f"{API}/me/reminders", headers=other).json() == []
    assert client.get(f"{API}/me/pipeline/summary", headers=other).json()["total_tracked"] == 0
    assert client.get(f"{API}/me/pipeline/export.csv", headers=other).text.count("\n") == 1
    assert client.get(f"{base}", headers=auth).status_code == 200            # y el dueño sigue viéndolo


def test_account_export_and_delete_cover_the_new_tables(client, flow):
    from sqlalchemy import text
    auth, jid, _ = flow
    client.post(f"{API}/me/jobs/{jid}/application/apply", json={"cover_letter": "hola"}, headers=auth)
    client.post(f"{API}/me/jobs/{jid}/prep", json={"parts": ["ask"]}, headers=auth)
    other = register(client, "otra@x.com")
    exp = client.get(f"{API}/me/export", headers=auth).json()["data"]
    assert len(exp["applicationrow"]) == 1 and len(exp["applicationarchive"]) == 1 and len(exp["preprow"]) == 1
    assert exp["applicationarchive"][0]["cover_letter"] == "hola"
    assert all(v == [] for k, v in client.get(f"{API}/me/export", headers=other).json()["data"].items() if k in ("applicationrow", "applicationarchive", "preprow"))
    assert client.delete(f"{API}/me", headers=auth).status_code == 204
    with Session(engine) as s:
        for t in ("applicationrow", "applicationarchive", "preprow"):
            assert s.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() == 0     # noqa: S608
