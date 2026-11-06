"""Infraestructura de scraping: HTTP (reintentos, caché, escalado), rate limit, normalización, salud y servicio."""
from datetime import date

import pytest
from sqlmodel import Session

from app.db import engine
from app.domain.geo import effective_countries, guess_country, portal_hosts
from app.domain.job import Job, SearchQuery
from app.scout import fetch, health, normalize
from app.scout.detect import detect_kind
from app.scout.ratelimit import RateLimiter, TokenBucket
from app.scout.service import run_search


@pytest.fixture(autouse=True)
def quiet_http(monkeypatch):
    monkeypatch.setattr(fetch, "_sleep", lambda s: None)
    monkeypatch.setattr(fetch.limiter, "enabled", False)
    fetch.reset_cache()
    yield
    fetch.reset_cache()


BIG = "<html><body>" + "contenido real " * 40 + "</body></html>"


def transport(monkeypatch, responses):
    calls = []

    def fake(url, *, stealth=False, **kw):
        calls.append((url, stealth))
        r = responses[min(len(calls) - 1, len(responses) - 1)] if not stealth else responses_stealth.get("r")
        if isinstance(r, Exception):
            raise r
        return r
    responses_stealth = {}
    monkeypatch.setattr(fetch, "_transport", fake)
    return calls, responses_stealth


def test_retries_with_backoff_then_succeeds(monkeypatch):
    calls, _ = transport(monkeypatch, [fetch.Resp(503, ""), fetch.Resp(429, "", {"retry-after": "1"}), fetch.Resp(200, BIG)])
    assert fetch.get_html("https://a.test/x") == BIG
    assert len(calls) == 3


def test_network_error_raises_fetcherror_after_attempts(monkeypatch):
    calls, _ = transport(monkeypatch, [OSError("dns")])
    with pytest.raises(fetch.FetchError):
        fetch.get_html("https://a.test/x")
    assert len(calls) == fetch.MAX_ATTEMPTS


def test_404_is_notfound_without_retry(monkeypatch):
    calls, _ = transport(monkeypatch, [fetch.Resp(404, "nope")])
    with pytest.raises(fetch.NotFound):
        fetch.get_html("https://a.test/gone")
    assert len(calls) == 1


def test_cache_avoids_repeated_requests(monkeypatch):
    calls, _ = transport(monkeypatch, [fetch.Resp(200, BIG)])
    fetch.get_html("https://a.test/c")
    fetch.get_html("https://a.test/c")
    assert len(calls) == 1


def test_escalates_to_stealth_on_403_and_captcha(monkeypatch):
    calls, st = transport(monkeypatch, [fetch.Resp(403, "denied")])
    st["r"] = fetch.Resp(200, BIG)
    assert fetch.get_html("https://a.test/b") == BIG
    assert calls[-1][1] is True
    fetch.reset_cache()
    calls, st = transport(monkeypatch, [fetch.Resp(200, "<html>Just a moment... captcha</html>")])
    st["r"] = fetch.Resp(200, BIG)
    assert fetch.get_html("https://a.test/d") == BIG


def test_json_does_not_escalate(monkeypatch):
    calls, _ = transport(monkeypatch, [fetch.Resp(200, '{"ok": 1}')])
    assert fetch.get_json("https://a.test/j") == {"ok": 1}
    calls, _ = transport(monkeypatch, [fetch.Resp(403, "x")])
    fetch.reset_cache()
    with pytest.raises(fetch.FetchError):
        fetch.get_json("https://a.test/j2")
    assert all(not s for _, s in calls)


def test_resolve_redirect_stops_at_external(monkeypatch):
    hops = {"https://mx.indeed.com/applystart?jk=1": fetch.Resp(302, "", {"location": "https://acme.eightfold.ai/job/1"})}
    monkeypatch.setattr(fetch, "_transport", lambda url, **kw: hops[url])
    assert fetch.resolve_redirect("https://mx.indeed.com/applystart?jk=1") == "https://acme.eightfold.ai/job/1"


def test_token_bucket_is_thread_safe_and_paces():
    now = [0.0]
    b = TokenBucket(rate=2, burst=1, clock=lambda: now[0])
    waits = [b.reserve() for _ in range(4)]
    assert waits == pytest.approx([0, 0.5, 1.0, 1.5])
    now[0] = 10
    assert b.reserve() == 0


def test_rate_limiter_is_per_host():
    slept = []
    rl = RateLimiter(limits={"slow.test": (1, 1)}, fallback=(100, 100), clock=lambda: 0.0, sleep=slept.append)
    rl.acquire("https://slow.test/a")
    rl.acquire("https://slow.test/b")
    rl.acquire("https://fast.test/a")
    assert len(slept) == 1 and slept[0] == pytest.approx(1.0)


# ---------------------------------------------------------------- normalización
TODAY = date(2026, 10, 7)


def test_dates():
    assert normalize.parse_date("hace 20 días", TODAY) == "2026-09-17"
    assert normalize.parse_date("Publicado ayer", TODAY) == "2026-10-06"
    assert normalize.parse_date("18 sept 2026", TODAY) == "2026-09-18"
    assert normalize.parse_date("Fri Sep 18 07:00:00 UTC 2026") == "2026-09-18"
    assert normalize.parse_date("1791392886") == "2026-10-07"
    assert normalize.parse_date("Wed, 07 Oct 2026 07:31:04 +0000") == "2026-10-07"
    assert normalize.parse_date("basura") is None


def test_salary():
    s = normalize.parse_salary("$20,000 a $25,000 por mes", "MX")
    assert (s["min"], s["max"], s["currency"], s["period"]) == (20000, 25000, "MXN", "month")
    s = normalize.parse_salary("COP 3.500.000 - 4.000.000")
    assert (s["min"], s["max"], s["currency"]) == (3_500_000, 4_000_000, "COP")
    s = normalize.parse_salary("USD 80k-100k per year")
    assert (s["min"], s["max"], s["period"]) == (80000, 100000, "year")
    assert normalize.parse_salary("3 años de experiencia", require_marker=True) is None


def test_language_and_work_mode():
    assert normalize.detect_language("Buscamos un analista con experiencia en el área de datos para nuestro equipo, con conocimientos de SQL y Python en la empresa") == "es"
    assert normalize.detect_language("We are looking for an engineer with experience in the team to work with our customers and you will be part of the role") == "en"
    assert normalize.detect_language("corto") is None
    assert normalize.detect_work_mode("Analista (Híbrido)") == "hybrid"
    assert normalize.detect_work_mode("100% remoto") == "remote"
    assert normalize.detect_work_mode("Trabajo presencial en Bogotá") == "onsite"


def test_finalize_fills_fields_and_marks_closed():
    j = Job(title="Analista", location="Medellín, Antioquia", url="u", source="s", posted_at="hace 3 días", salary="$ 4.000.000 mensuales",
            description="Buscamos un analista con experiencia en datos para nuestro equipo. Modalidad remoto. " * 3)
    normalize.finalize(j, today=TODAY)
    assert j.country == "CO" and j.language == "es" and j.work_mode == "remote" and j.remote and j.posted_at
    assert j.salary_min == 4_000_000 and j.salary_currency == "COP" and j.salary_period == "month"
    old = Job(title="X", url="u", source="s", expires_at="2026-01-01", description="d")
    assert normalize.finalize(old, today=TODAY).closed
    gone = Job(title="X", url="u", source="s", description="Esta oferta ya no está disponible")
    assert normalize.finalize(gone, today=TODAY).closed


def test_closed_and_language_filtered_by_service():
    class Src:
        name, countries = "s", set()

        def search(self, q):
            return [Job(title="Abierta", company="A", url="1", source="s", description="Buscamos un analista con experiencia en el área de datos para nuestro equipo en la empresa y con conocimientos de SQL"),
                    Job(title="Cerrada", company="B", url="2", source="s", closed=True),
                    Job(title="English", company="C", url="3", source="s", description="We are looking for an engineer with experience in the team to work with our customers and you will be part of the role")]
    jobs, errors = run_search(SearchQuery(keywords=["x"], languages=["es"]), [Src()])
    assert [j.title for j in jobs] == ["Abierta"] and not errors


# ---------------------------------------------------------------- geo / detect
def test_geo_routing():
    assert portal_hosts("computrabajo")["PE"] == "pe" and "VE" in portal_hosts("computrabajo")
    assert set(portal_hosts("magneto")) == {"CO"} and set(portal_hosts("elempleo")) == {"CO"}
    assert effective_countries([], {"CO", "MX"}) == ["CO"] and effective_countries(["MX", "BR"], {"CO", "MX"}) == ["MX"]
    assert guess_country("Medellín, Antioquia, Colombia") == "CO" and guess_country("Remote") is None


def test_detect_kind():
    assert detect_kind("https://jobs.ashbyhq.com/nubank") == ("ashby", "nubank")
    assert detect_kind("https://apply.workable.com/platzi/") == ("workable", "platzi")
    assert detect_kind("https://jobs.smartrecruiters.com/Endava") == ("smartrecruiters", "Endava")
    assert detect_kind("https://nvidia.wd5.myworkdayjobs.com/en-US/Site") == ("workday", "nvidia|wd5|Site")
    assert detect_kind("https://bunq.recruitee.com/") == ("recruitee", "bunq")
    assert detect_kind("https://instructure.bamboohr.com/careers") == ("bamboohr", "instructure")
    assert detect_kind("https://x.com/careers") == ("careers", None)


# ---------------------------------------------------------------- salud
def test_health_marks_silent_zero_as_degraded():
    health.record("portal_x", 0, None, 0.1, canary=True)
    row = next(h for h in health.get_health() if h["source"] == "portal_x")
    assert row["status"] == "degraded"
    health.record("portal_x", 5, None, 0.1)
    assert next(h for h in health.get_health() if h["source"] == "portal_x")["status"] == "ok"


def test_health_real_runs_need_threshold_and_zero_ok_never_degrades():
    for _ in range(health.ZERO_THRESHOLD - 1):
        health.record("p2", 0, None, 0.1)
    assert next(h for h in health.get_health() if h["source"] == "p2")["status"] == "empty"      # vacía (respondió bien), aún no degradada
    health.record("p2", 0, None, 0.1)
    assert next(h for h in health.get_health() if h["source"] == "p2")["status"] == "degraded"
    health.record("board", 0, None, 0.1, zero_ok=True, canary=True)
    assert next(h for h in health.get_health() if h["source"] == "board")["status"] == "ok"
    health.record("p3", 0, "HTTP 500", 0.1)
    assert next(h for h in health.get_health() if h["source"] == "p3")["status"] == "error"


def test_check_source_detects_error_zero_and_empty_quality():
    class Boom:
        name, countries = "boom", set()

        def search(self, q): raise RuntimeError("bloqueado")

    class Zero(Boom):
        name = "zero"

        def search(self, q): return []

    class Hollow(Boom):
        name = "hollow"

        def search(self, q): return [Job(title="a", url="1", source="h"), Job(title="b", url="2", source="h")]
    assert health.check_source(Boom(), persist=False)["status"] == "error"
    assert health.check_source(Zero(), persist=False)["status"] == "degraded"
    assert health.check_source(Hollow(), persist=False)["status"] == "degraded"


def test_seed_companies_is_idempotent():
    from scripts.seed_companies import seed
    with Session(engine) as s:
        c1, _ = seed(s)
        c2, u2 = seed(s)
    assert c1 >= 15 and (c2, u2) == (0, 0)
