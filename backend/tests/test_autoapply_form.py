"""Motor de formularios contra páginas locales REALES (Playwright + servidor http): rellena lo que reconoce, se detiene ante lo desconocido,
no resuelve captchas, no marca casillas de marketing y distingue ensayo de envío."""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytest.importorskip("playwright")


def _chromium_ok() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _chromium_ok(), reason="Chromium de Playwright no instalado (playwright install chromium)")
from app.autoapply.form import Applicant, run_form  # noqa: E402
from app.autoapply.models import QA, ApplyData  # noqa: E402

LEVER = """<html><body><form method="post" action="/submit" enctype="multipart/form-data">
<label>Full name <input name="name" required></label><label>Email <input type="email" name="email" required></label>
<label>Phone <input name="phone"></label><label>Current company <input name="org"></label>
<label>LinkedIn URL <input name="urls[LinkedIn]"></label><label>Resume/CV <input type="file" name="resume" required></label>
<label><input type="checkbox" name="mk"> Send me marketing emails</label>
<button type="submit">Submit application</button></form></body></html>"""
UNKNOWN = LEVER.replace("<label>Phone", "<label>Why do you love our unicorn mission? <input name=q required></label><label>Phone")
CAPTCHA = LEVER.replace("<button", '<div class="g-recaptcha" data-sitekey="x"></div><button')
PRIVACY = LEVER.replace("<button", '<label><input type="checkbox" name="pp" required> I accept the privacy policy</label><button')
QA_FORM = LEVER.replace("<label>Phone", "<label>Years of experience with Python? <input name=py required></label><label>Phone")
HITS: list[dict] = []


class H(BaseHTTPRequestHandler):
    pages = {"/lever": LEVER, "/unknown": UNKNOWN, "/captcha": CAPTCHA, "/privacy": PRIVACY, "/qa": QA_FORM}

    def log_message(self, *a): ...

    def do_GET(self):
        body = self.pages.get(self.path.split("?")[0], "<html><body>nada</body></html>").encode()
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        HITS.append({"path": self.path, "raw": raw})
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write(b"<html><body><h1>Thank you for applying!</h1></body></html>")


@pytest.fixture(scope="module")
def base():
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def cv(tmp_path):
    p = tmp_path / "cv.pdf"
    p.write_bytes(b"%PDF-1.4 contenido de prueba")
    return str(p)


def me(**kw) -> Applicant:
    return Applicant(full_name="Luis Gómez", email="luis@x.co", phone="+57 300 123 4567", city="Bogotá", data=ApplyData(linkedin_url="https://linkedin.com/in/luis", **kw))


def test_dry_run_fills_everything_but_does_not_submit(base, cv):
    HITS.clear()
    r = run_form(f"{base}/lever", me(), cv, dry_run=True)
    assert r.status == "dry_run" and not HITS
    assert "CV adjunto" in r.filled and "email" in r.filled and "linkedin" in r.filled


def test_real_run_submits_with_resume_and_confirms_and_skips_marketing(base, cv):
    HITS.clear()
    r = run_form(f"{base}/lever", me(), cv, dry_run=False)
    assert r.status == "submitted", r.message
    raw = HITS[0]["raw"]
    assert b"Luis" in raw and b"luis@x.co" in raw and b"%PDF-1.4" in raw and b'name="mk"' not in raw      # CV adjunto y la casilla de marketing sin marcar


def test_unknown_required_question_stops_and_is_never_invented(base, cv):
    HITS.clear()
    r = run_form(f"{base}/unknown", me(), cv, dry_run=False)
    assert r.status == "needs_manual" and not HITS and any("unicorn" in u for u in r.unanswered)


def test_captcha_is_not_solved(base, cv):
    HITS.clear()
    r = run_form(f"{base}/captcha", me(), cv, dry_run=False)
    assert r.status == "needs_manual" and "captcha" in r.message.lower() and not HITS


def test_privacy_checkbox_only_with_explicit_permission(base, cv):
    HITS.clear()
    blocked = run_form(f"{base}/privacy", me(), cv, dry_run=False)
    assert blocked.status == "needs_manual" and not HITS
    ok = run_form(f"{base}/privacy", me(accept_privacy_notices=True), cv, dry_run=False)
    assert ok.status == "submitted" and b'name="pp"' in HITS[0]["raw"]


def test_user_fixed_answers_fill_custom_questions(base, cv):
    HITS.clear()
    r = run_form(f"{base}/qa", me(answers=[QA(pattern="years of experience with python", answer="6")]), cv, dry_run=False)
    assert r.status == "submitted" and b"6" in HITS[0]["raw"]


def test_page_without_form_asks_the_user_to_apply_manually(base, cv):
    r = run_form(f"{base}/vacio", me(), cv, dry_run=False)
    assert r.status == "needs_manual"


def test_end_to_end_real_browser_real_pdf_and_tracking(base, client, ready, monkeypatch):
    """Todo el camino sin dobles de formulario: política auto -> intento -> PDF real del CV -> navegador -> envío -> seguimiento."""
    from sqlmodel import Session, select

    from app.autoapply import service as svc
    from app.db import ApplicationRow, ApplyAttempt, JobRow, User, engine
    from tests.shared import API
    monkeypatch.setattr("app.scout.netguard.assert_public_url", lambda u: u)
    client.put(f"{API}/me/auto-apply/policy", json={"mode": "auto", "dry_run": False, "cv_mode": "base", "min_score": 3.5}, headers=ready)
    client.put(f"{API}/me/auto-apply/data", json={"full_name": "Luis Gomez", "email": "luis@x.com", "phone": "+57 300 1112233"}, headers=ready)
    HITS.clear()
    with Session(engine) as s:
        uid = s.exec(select(User)).first().id
        row = JobRow(user_id=uid, dedup_key="e2e", evaluation={"score": 4.6, "recommendation": "apply", "vetoes": [], "dimensions": []},
                     data={"title": "Dev Python", "company": "Acme", "source": "lever:acme", "url": f"{base}/lever", "description": "Python"})
        s.add(row)
        s.commit()
        out = svc.on_new_jobs(s, s.get(User, uid), [row])
        jid = row.id
    assert out["queued"] == 1
    with Session(engine) as s:
        a = s.exec(select(ApplyAttempt)).first()
        assert a.status == "submitted", (a.status, a.error, a.detail)
        assert s.exec(select(ApplicationRow).where(ApplicationRow.job_id == jid)).first().applied_at
    assert b"%PDF" in HITS[0]["raw"] and b"luis@x.com" in HITS[0]["raw"]
