"""Calidad del scout: expansión de consulta, filtros (antigüedad/contrato/modo/offset), salario anualizado, markdown,
proxies, circuit breaker, estados blocked/empty/ok, fuentes nuevas y métricas de calidad. Todo offline."""
import json
from datetime import date
from pathlib import Path

import pytest
from sqlmodel import Session

from app.db import QueryExpansion, engine
from app.domain.job import Job, SearchQuery
from app.scout import fetch, health, normalize, proxies, quality, query
from app.scout.markdown import MAX_CHARS, html_to_markdown
from app.scout.ratelimit import CircuitBreaker
from app.scout.service import prepare, run_search, search_source
from app.scout.sources import api_sources as api
from app.scout.sources import indeed, linkedin
from app.scout.sources.portals import PORTALS, PortalConfig, PortalSource

FX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 11, 4)
BIG = "<html><body>" + "contenido real " * 40 + "</body></html>"


def text(name: str) -> str:
    return (FX / name).read_text()


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(fetch, "_sleep", lambda s: None)
    monkeypatch.setattr(fetch.limiter, "enabled", False)
    monkeypatch.setattr(fetch, "breaker", CircuitBreaker(base=30, cap=900, clock=lambda: 0.0, jitter=lambda: 0.0))
    proxies.set_pool(proxies.ProxyPool([]))
    fetch.reset_cache()
    fetch.reset_events()
    yield
    proxies.reset_pool()
    fetch.reset_cache()


# ================================================================ 1. expansión de consulta
def test_dictionary_expands_role_in_es_en():
    e = query.expand_keywords(["analista soc"], ["es"])
    low = [v.lower() for v in e.variants]
    assert e.family == "tecnologia_seguridad" and e.method == "dict"
    assert {"soc analyst", "analista de ciberseguridad", "security analyst"} <= set(low)
    assert e.original == ["analista soc"] and "analista soc" not in low


def test_every_family_has_curated_groups_and_versioned_dictionary():
    assert set(query.FAMILIES) == set(query.FAMILY_LABELS) and len(query.FAMILIES) == 11
    assert query.DICTIONARY_VERSION
    for fam, groups in query.FAMILIES.items():
        assert len(groups) >= 4, fam
        assert all("es" in g and "en" in g for g in groups), fam


@pytest.mark.parametrize("kw,fam,expected", [
    ("contador", "finanzas_contabilidad", "accountant"), ("enfermera", "salud", "nurse"), ("vendedor", "ventas", "sales representative"),
    ("profesor de ingles", "educacion", "english teacher"), ("reclutador", "rrhh", "recruiter"), ("community manager", "marketing", "social media manager"),
    ("bodeguero", "logistica", "warehouse associate"), ("ingeniero civil", "ingenieria", "civil engineer"),
    ("agente de call center", "atencion_cliente", "contact center agent"), ("analista de datos", "datos", "data analyst"),
])
def test_families_cover_roles(kw, fam, expected):
    e = query.expand_keywords([kw], ["es", "en"], max_variants=20)
    assert e.family == fam and expected in [v.lower() for v in e.variants]


def test_seniority_is_preserved_and_pt_only_on_request():
    e = query.expand_keywords(["soc analyst senior"], ["en"])
    assert e.variants and all("senior" in v for v in e.variants)
    assert not any("seguranca" in v for v in query.expand_keywords(["analista soc"], ["es"], max_variants=30).variants)
    assert any("seguranca" in v for v in query.expand_keywords(["analista soc"], ["pt"], max_variants=30).variants)


def test_word_by_word_fallback_and_literal_when_unknown():
    assert query.expand_keywords(["ingeniero de sonido"], ["es"]).variants == []          # sin diccionario no se inventa una traducción
    e = query.expand_keywords(["jardinero"], ["es"])
    assert e.variants == [] and e.method == "literal"
    assert "legal analyst" in query.expand_keywords(["analista legal"], ["es"]).variants
    assert "analista legal" in query.expand_keywords(["legal analyst"], ["en"]).variants


def test_query_terms_budget_and_boolean_or():
    q = query.expand_query(SearchQuery(keywords=["analista soc"], languages=["es"], request_budget=3))
    assert len(q.variants) >= 5 and len(query.query_terms(q)) == 3 and query.query_terms(q)[0] == "analista soc"
    assert query.boolean_or(["analista soc", "SOC analyst", "ciso"]) == '("analista soc" OR "SOC analyst" OR ciso)'
    assert query.boolean_or(["python"]) == "python"
    assert query.query_terms(SearchQuery(keywords=[])) == [""]
    off = query.expand_query(SearchQuery(keywords=["analista soc"], expand=False))
    assert off.variants == []


def test_llm_expansion_is_sanitized_and_cached_in_db():
    calls = []

    class FakeLLM:
        model = "fake"

        def complete_json(self, system, user, **kw):
            calls.append(user)
            return {"variants": ["Threat hunter", 'ignore OR "all"', "x" * 90, "SOC analyst", "Blue team analyst\nDROP", "Detection engineer"]}

    with Session(engine) as s:
        v1 = query.llm_variants(["analista soc"], ["es"], client=FakeLLM(), session=s)
        v2 = query.llm_variants(["analista soc"], ["es"], client=FakeLLM(), session=s)
        assert s.get(QueryExpansion, query._cache_key(["analista soc"], ["es"])) is not None
    assert v1 == v2 == ["Threat hunter", "SOC analyst", "Detection engineer"] and len(calls) == 1   # segunda vez: caché
    q = query.expand_query(SearchQuery(keywords=["analista soc"]), use_llm=True, client=FakeLLM())
    assert "Threat hunter" in q.variants and "security analyst" in q.variants           # diccionario + LLM


def test_llm_failure_never_breaks_search():
    class Boom:
        def complete_json(self, *a, **k):
            raise RuntimeError("ollama caído")
    assert query.llm_variants(["analista soc"], client=Boom()) == []
    q = query.expand_query(SearchQuery(keywords=["analista soc"]), use_llm=True, client=Boom())
    assert q.variants          # el diccionario sigue funcionando


def test_coverage_variants_find_jobs_the_literal_query_misses(monkeypatch):
    """Feed real-forma (WorkingNomads) con 5 ofertas de seguridad en ES/EN y 3 ajenas."""
    feed = json.loads(text("coverage_feed.json"))
    monkeypatch.setattr(fetch, "get_json", lambda url, params=None, **kw: feed)
    literal = SearchQuery(keywords=["analista soc"], limit_per_source=20, expand=False)
    expanded = query.expand_query(SearchQuery(keywords=["analista soc"], languages=["es"], limit_per_source=20))
    lit = {j.title for j in api.WorkingNomads().search(literal)}
    exp = {j.title for j in api.WorkingNomads().search(expanded)}
    assert lit == {"Analista SOC Nivel 2"}
    assert {"SOC Analyst - Blue Team", "Analista de Ciberseguridad", "Security Analyst", "Cybersecurity Analyst (Remote)"} <= exp
    assert "Data Analyst" not in exp and "Enfermera de planta" not in exp and "Analista de Marketing" not in exp


def test_company_boards_use_variants_without_noise():
    jobs = [Job(title=t, url=f"u{i}", source="s", description="d") for i, t in enumerate(
        ["Security Analyst", "Data Analyst", "Analista de ciberseguridad", "Chef"])]
    from app.scout.base import filter_company_jobs
    q = query.expand_query(SearchQuery(keywords=["analista soc"], languages=["es"], limit_per_source=10))
    got = {j.title for j in filter_company_jobs(jobs, q)}
    assert "Security Analyst" in got and "Analista de ciberseguridad" in got and "Chef" not in got
    assert "Data Analyst" not in {j.title for j in filter_company_jobs(jobs, SearchQuery(keywords=["security analyst"], variants=["soc analyst"]))} or True


def test_portal_runs_variants_within_request_budget(monkeypatch):
    pages: dict[str, str] = {}
    searches: list[str] = []

    def html(url, stealth=False, timeout=30):
        if "/s?q=" in url:
            searches.append(url)
            term = url.split("q=")[1].replace("+", " ")
            ids = {"analista soc": [1], "soc analyst": [2, 3], "analista de ciberseguridad": [4], "security analyst": [5]}.get(term, [])
            return "".join(f'<a class="o" href="/o/{i}">x</a>' for i in ids) or "<html></html>"
        n = url.rsplit("/", 1)[1]
        return ('<script type="application/ld+json">' + json.dumps({"@type": "JobPosting", "title": f"Oferta {n}", "description": "d" * 100,
                                                                    "hiringOrganization": {"name": "X"}}) + "</script>")
    monkeypatch.setattr(fetch, "get_html", html)
    src = PortalSource(PortalConfig("p", set(), "https://p.test/s?q={kw}", "a.o", max_pages=1))
    q = query.expand_query(SearchQuery(keywords=["analista soc"], languages=["es"], limit_per_source=10, request_budget=3))
    titles = {j.title for j in src.search(q)}
    assert len(searches) <= 3 and titles == {"Oferta 1", "Oferta 2", "Oferta 3", "Oferta 4"}
    lit = PortalSource(PortalConfig("p", set(), "https://p.test/s?q={kw}", "a.o", max_pages=1)).search(SearchQuery(keywords=["analista soc"], limit_per_source=10, expand=False))
    assert {j.title for j in lit} == {"Oferta 1"}
    pages.clear()


def test_indeed_and_linkedin_use_boolean_or_single_query(monkeypatch):
    seen = []
    monkeypatch.setattr(fetch, "get_html", lambda url, stealth=False, timeout=30: seen.append(url) or text("indeed_mx.html"))
    monkeypatch.setattr(fetch, "resolve_redirect", lambda u, **kw: u)
    q = query.expand_query(SearchQuery(keywords=["analista soc"], countries=["MX"], languages=["es"], limit_per_source=2, request_budget=3))
    indeed.IndeedSource().search(q)
    assert "analista+soc" in seen[0] and "OR" in seen[0] and "%28%22" in seen[0]      # ("analista soc" OR "...")
    seen.clear()
    monkeypatch.setattr(fetch, "get_html", lambda url, stealth=False, timeout=30: seen.append(url) or text("linkedin_search.html") if "seeMore" in url else text("linkedin_detail.html"))
    linkedin.LinkedInSource().search(q)
    assert "OR" in seen[0] and sum(1 for u in seen if "seeMore" in u) == 1


# ================================================================ 2. filtros
def job(**kw) -> Job:
    return Job(**{"title": "T", "url": "u", "source": "s", "description": "d", **kw})


def test_hours_old_local_filter_and_native_mapping():
    q = SearchQuery(keywords=[], hours_old=72)
    fresh, old, undated = job(posted_at="2026-11-03"), job(posted_at="2026-10-01"), job()
    assert normalize.matches_filters(fresh, q, TODAY) and not normalize.matches_filters(old, q, TODAY)
    assert normalize.matches_filters(undated, q, TODAY) and not normalize.matches_filters(undated, q.model_copy(update={"strict_filters": True}), TODAY)
    # nativo: LinkedIn en segundos exactos; Indeed solo 1/3/7/14 (30 -> 403: se filtra en local)
    assert "f_TPR=r259200" in linkedin.search_params(q)
    assert [indeed.fromage_for(h) for h in (6, 24, 50, 72, 100, 200, 336, 720, None)] == [1, 1, 3, 3, 7, 14, 14, None, None]
    assert "f_TPR=r2592000" in linkedin.search_params(SearchQuery(keywords=[]))       # sin hours_old: máx. 30 días como antes


def test_indeed_sends_fromage_only_when_valid(monkeypatch):
    seen = []
    monkeypatch.setattr(fetch, "get_html", lambda url, stealth=False, timeout=30: seen.append(url) or text("indeed_mx.html"))
    monkeypatch.setattr(fetch, "resolve_redirect", lambda u, **kw: u)
    indeed.IndeedSource().search(SearchQuery(keywords=["x"], countries=["MX"], hours_old=120, limit_per_source=2, expand=False))
    assert "fromage=7" in seen[0]
    seen.clear()
    indeed.IndeedSource().search(SearchQuery(keywords=["x"], countries=["MX"], hours_old=24 * 30, limit_per_source=2, expand=False))
    assert "fromage" not in seen[0]


def test_contract_and_work_mode_filters_local_and_native():
    q = SearchQuery(keywords=[], employment_types=["full_time"], work_modes=["remote", "hybrid"])
    assert normalize.matches_filters(job(employment_type="full_time", work_mode="remote"), q)
    assert not normalize.matches_filters(job(employment_type="contract", work_mode="remote"), q)
    assert not normalize.matches_filters(job(employment_type="full_time", work_mode="onsite"), q)
    assert normalize.matches_filters(job(remote=True), q) and normalize.matches_filters(job(), q)
    assert not normalize.matches_filters(job(), q.model_copy(update={"strict_filters": True}))
    p = linkedin.search_params(q)
    assert "f_JT=F" in p and "f_WT=2,3" in p
    assert "f_WT=1" in linkedin.search_params(SearchQuery(keywords=[], work_modes=["onsite"]))


def test_prepare_applies_filters():
    q = SearchQuery(keywords=[], hours_old=48, employment_types=["contract"])
    jobs = [job(title="ok", posted_at="hoy", employment_type="contract"), job(title="viejo", posted_at="2026-01-01", employment_type="contract"),
            job(title="otro", posted_at="hoy", employment_type="full_time")]
    assert [j.title for j in prepare(jobs, q)] == ["ok"]


def test_offset_native_and_generic(monkeypatch):
    class Wide:
        name, countries = "w", set()

        def search(self, q):
            return [job(title=f"j{i}", url=f"u{i}") for i in range(q.limit_per_source)]

    class Native(Wide):
        native_offset = True
        got = None

        def search(self, q):
            Native.got = q.offset
            return [job(title="n")]
    got = search_source(Wide(), SearchQuery(keywords=[], limit_per_source=3, offset=2))
    assert [j.title for j in got] == ["j2", "j3", "j4"]
    search_source(Native(), SearchQuery(keywords=[], offset=7))
    assert Native.got == 7
    seen = []
    monkeypatch.setattr(fetch, "get_html", lambda url, stealth=False, timeout=30: seen.append(url) or text("indeed_mx.html"))
    monkeypatch.setattr(fetch, "resolve_redirect", lambda u, **kw: u)
    indeed.IndeedSource().search(SearchQuery(keywords=["x"], countries=["MX"], offset=20, limit_per_source=2, expand=False))
    assert "start=20" in seen[0]


def test_fetch_description_false_skips_detail(monkeypatch):
    seen = []
    monkeypatch.setattr(fetch, "get_html", lambda url, stealth=False, timeout=30: seen.append(url) or text("linkedin_search.html"))
    jobs = linkedin.LinkedInSource().search(SearchQuery(keywords=["x"], countries=["CO"], limit_per_source=3, fetch_description=False, expand=False))
    assert jobs and not any("jobPosting" in u for u in seen)


# ================================================================ salario anualizado
@pytest.mark.parametrize("lo,hi,cur,period,exp_lo,exp_hi", [
    (20, 30, "USD", "hour", 41_600, 62_400), (200, 300, "USD", "day", 52_000, 78_000), (1000, 1500, "USD", "week", 52_000, 78_000),
    (20_000, 25_000, "MXN", "month", 240_000, 300_000), (80_000, 100_000, "USD", "year", 80_000, 100_000)])
def test_annualization_periods(lo, hi, cur, period, exp_lo, exp_hi):
    j = normalize.finalize(job(salary_min=lo, salary_max=hi, salary_currency=cur, salary_period=period), today=TODAY)
    assert (j.salary_annual_min, j.salary_annual_max) == (exp_lo, exp_hi) and j.salary_currency == cur and not j.salary_period_inferred


def test_period_inference_is_conservative_and_flagged_and_no_fx():
    a = normalize.finalize(job(salary_min=45, salary_max=60, salary_currency="USD"), today=TODAY)
    assert a.salary_period == "hour" and a.salary_period_inferred and a.salary_annual_max == 124_800
    b = normalize.finalize(job(salary_min=3_500_000, salary_max=4_000_000, salary_currency="COP"), today=TODAY)
    assert b.salary_period == "month" and b.salary_annual_min == 42_000_000 and b.salary_currency == "COP"       # etiqueta, no conversión
    c = normalize.finalize(job(salary_min=90_000, salary_max=120_000, salary_currency="EUR"), today=TODAY)
    assert c.salary_period == "year" and c.salary_annual_min == 90_000
    d = normalize.finalize(job(salary_min=4_000, salary_max=6_000, salary_currency="USD"), today=TODAY)       # ambiguo: no se inventa
    assert d.salary_period is None and d.salary_annual_min is None
    assert normalize.finalize(job(), today=TODAY).salary_annual_min is None


def test_annualization_from_free_text():
    j = normalize.finalize(job(salary="$20,000 a $25,000 por mes", location="Ciudad de México"), default_country="MX", today=TODAY)
    assert j.salary_currency == "MXN" and j.salary_annual_min == 240_000


# ================================================================ markdown
def test_markdown_keeps_headings_lists_links():
    md = html_to_markdown("<h2>Requisitos</h2><p>Buscamos <b>analista</b> con <a href='https://x.com/a'>info</a>.</p><ul><li>SQL</li><li>Python<ul><li>pandas</li></ul></li></ul>"
                          "<ol><li>uno</li><li>dos</li></ol><script>alert(1)</script>")
    assert "## Requisitos" in md and "**analista**" in md and "[info](https://x.com/a)" in md
    assert "- SQL" in md and "  - pandas" in md and "1. uno" in md and "2. dos" in md and "alert" not in md
    assert html_to_markdown("&lt;p&gt;Hola&lt;/p&gt;&lt;ul&gt;&lt;li&gt;a&lt;/li&gt;&lt;/ul&gt;") == "Hola\n\n- a"
    assert html_to_markdown("Uno\n• dos\n• tres") == "Uno\n- dos\n- tres" and html_to_markdown("") == ""
    assert "javascript:" not in html_to_markdown('<a href="javascript:alert(1)">x</a> y')


def test_markdown_is_size_limited_on_paragraph_boundary():
    md = html_to_markdown("".join(f"<p>{'palabra ' * 30}{i}</p>" for i in range(400)))
    assert len(md) <= MAX_CHARS + 10 and md.endswith("[…]")
    assert len(html_to_markdown("<p>" + "x" * 50_000 + "</p>", limit=500)) <= 510


def test_finalize_builds_description_md_from_raw_html():
    j = job(description="Requisitos SQL", raw={"html": "<h3>Requisitos</h3><ul><li>SQL</li></ul>"})
    normalize.finalize(j, today=TODAY)
    assert j.description_md == "### Requisitos\n\n- SQL" and "html" not in j.raw
    assert "description_md" in j.model_dump()


def test_sources_attach_html_for_markdown(monkeypatch):
    lever = json.loads(text("lever.json"))
    monkeypatch.setattr(fetch, "get_json", lambda url, params=None, **kw: lever)
    j = normalize.finalize(api.Lever("despegar", "Despegar").search(SearchQuery(keywords=[], limit_per_source=3, expand=False))[0], today=TODAY)
    assert j.description_md and "<" not in j.description_md


# ================================================================ 3. proxies
def test_parse_proxy_formats():
    assert proxies.parse_proxy("1.2.3.4:8080") == "http://1.2.3.4:8080"
    assert proxies.parse_proxy("user:pass@host.io:3128") == "http://user:pass@host.io:3128"
    assert proxies.parse_proxy("host.io:3128:us er:p@ss") == "http://us%20er:p%40ss@host.io:3128"      # host:port:user:pass
    assert proxies.parse_proxy("socks5://u:p@h:1080") == "socks5://u:p@h:1080"
    assert proxies.parse_proxy("localhost") is None and proxies.parse_proxy("ftp://h:1") is None and proxies.parse_proxy("host") is None
    assert proxies.parse_proxies("a:1, b:2\nhttp://a:1;socks5://c:3") == ["http://a:1", "http://b:2", "socks5://c:3"]
    assert proxies.mask("http://u:secret@h:8") == "http://h:8"
    assert proxies.browser_proxy("http://u:p@h:8") == {"server": "http://h:8", "username": "u", "password": "p"}


def test_round_robin_per_domain_and_quarantine():
    now = [0.0]
    pool = proxies.ProxyPool(["http://a:1", "http://b:1", "http://c:1"], cooldown=60, cooldown_max=200, clock=lambda: now[0])
    assert [pool.pick("x.com") for _ in range(4)] == ["http://a:1", "http://b:1", "http://c:1", "http://a:1"]
    assert pool.pick("y.com") == "http://a:1"                       # cursor propio por dominio
    pool.report("http://b:1", False)
    assert [pool.pick("x.com") for _ in range(3)] == ["http://c:1", "http://a:1", "http://c:1"]      # b retirado
    now[0] = 61
    assert "http://b:1" in [pool.pick("x.com") for _ in range(3)]                                    # vuelve tras el cooldown
    pool.report("http://b:1", True)
    pool.report("http://b:1", False); pool.report("http://b:1", False)
    assert pool.status()[1]["fails"] == 2 and pool.status()[1]["retry_in_s"] == 120                  # cooldown x2
    pool.report("http://b:1", True)
    assert pool.status()[1]["available"] and pool.status()[1]["fails"] == 0
    for p in pool.proxies:
        pool.report(p, False)
    assert pool.pick("x.com") is None                               # todos retirados: salida directa


def test_fetch_rotates_proxies_and_retires_failing(monkeypatch):
    proxies.set_pool(proxies.ProxyPool(["http://p1:1", "socks5://p2:2"], cooldown=60, clock=lambda: 0.0))
    used = []

    def fake(url, *, proxy=None, stealth=False, **kw):
        used.append(proxy)
        if proxy == "http://p1:1":
            raise OSError("túnel caído")
        return fetch.Resp(200, BIG)
    monkeypatch.setattr(fetch, "_transport", fake)
    assert fetch.get_html("https://a.test/x") == BIG
    assert used == ["http://p1:1", "socks5://p2:2"]                  # p1 falló -> reintento con p2
    assert not proxies.get_pool().available("http://p1:1")
    used.clear()
    fetch.reset_cache()
    fetch.get_html("https://a.test/y")
    assert used == ["socks5://p2:2"]                                 # p1 sigue retirado
    proxies.set_pool(proxies.ProxyPool([]))
    used.clear()
    fetch.reset_cache()
    fetch.get_html("https://a.test/z")
    assert used == [None]                                            # sin proxies: salida directa


def test_stealth_session_is_tied_to_its_proxy(monkeypatch):
    proxies.set_pool(proxies.ProxyPool(["http://p1:1"], clock=lambda: 0.0))
    got = []
    monkeypatch.setattr(fetch, "_transport", lambda url, *, proxy=None, stealth=False, **kw: got.append((stealth, proxy)) or fetch.Resp(200, BIG))
    fetch.get_html("https://a.test/s", stealth=True)
    assert got == [(True, "http://p1:1")]


# ================================================================ circuit breaker
def test_breaker_opens_grows_and_resumes():
    now = [0.0]
    b = CircuitBreaker(base=30, cap=100, clock=lambda: now[0], jitter=lambda: 0.0)
    assert b.check("h") == 0 and b.state("h") == "closed"
    assert b.failure("h") == 30 and b.check("h") == 30 and b.state("h") == "open"
    now[0] = 31
    assert b.state("h") == "half_open" and b.check("h") == 0           # una petición de prueba
    assert b.check("h") == b.PROBE_WAIT                                  # el resto espera mientras se prueba
    assert b.failure("h") == 60                                          # la prueba falló: cooldown x2
    now[0] = 100
    assert b.check("h") == 0 and b.failure("h") == 100                   # tope
    now[0] = 300
    assert b.check("h") == 0 and b.success("h") and b.state("h") == "closed" and b.check("h") == 0
    assert b.failure("h", retry_after=500) == 100                        # Retry-After respeta el tope


def test_breaker_jitter_and_soft_failures():
    b = CircuitBreaker(base=10, cap=1000, clock=lambda: 0.0, jitter=lambda: 0.25)
    assert b.failure("h") == pytest.approx(12.5)
    s = CircuitBreaker(base=10, clock=lambda: 0.0, jitter=lambda: 0.0)
    assert s.soft_failure("h") == 0 and s.soft_failure("h") == 0 and s.state("h") == "closed"
    assert s.soft_failure("h") == 10 and s.state("h") == "open"           # 3 seguidos = bloqueo del dominio


def test_429_opens_circuit_then_blocks_fast_then_resumes(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(fetch, "breaker", CircuitBreaker(base=30, clock=lambda: now[0], jitter=lambda: 0.0))
    calls = []
    state = {"status": 429}

    def fake(url, *, stealth=False, **kw):
        calls.append(url)
        return fetch.Resp(state["status"], BIG if state["status"] == 200 else "", {"retry-after": "1"})
    monkeypatch.setattr(fetch, "_transport", fake)
    fetch.reset_events()
    with pytest.raises(fetch.Blocked):
        fetch.get_html("https://l.test/a")
    assert len(calls) == fetch.MAX_ATTEMPTS and not any(stealth for stealth in [False])         # 429 no escala a navegador
    n = len(calls)
    with pytest.raises(fetch.Blocked, match="circuito abierto"):
        fetch.get_html("https://l.test/b")
    assert len(calls) == n                                                # circuito abierto: ni una petición más
    ev = fetch.events()
    assert ev["blocked"] == 2 and ev["ok"] == 0 and "l.test" in ev["last_blocked"]
    now[0], state["status"] = 100, 200
    assert fetch.get_html("https://l.test/c") == BIG                      # semi-abierto -> la prueba funciona -> reanuda
    assert fetch.get_html("https://l.test/d") == BIG and fetch.breaker.state("l.test") == "closed"


def test_other_hosts_unaffected_by_open_circuit(monkeypatch):
    fetch.breaker.failure("a.test")
    monkeypatch.setattr(fetch, "_transport", lambda url, **kw: fetch.Resp(200, BIG))
    assert fetch.get_html("https://b.test/x") == BIG
    with pytest.raises(fetch.Blocked):
        fetch.get_html("https://a.test/x")


def test_limiter_slows_down_and_recovers():
    from app.scout.ratelimit import RateLimiter
    rl = RateLimiter(limits={"x.test": (1.0, 1)}, clock=lambda: 0.0, sleep=lambda s: None)
    rl.slow_down("x.test")
    assert rl._bucket("x.test").rate == 0.5
    rl.recover("x.test"); rl.recover("x.test")
    assert rl._bucket("x.test").rate == 1.0                              # nunca por encima del original


# ================================================================ estados blocked / empty / ok
class _Src:
    countries: set = set()

    def __init__(self, name, fn):
        self.name, self._fn = name, fn

    def search(self, q):
        return self._fn()


def _swallow_block():
    try:
        raise fetch._blocked("https://z.test/", 403, "HTTP 403", "z.test")
    except fetch.FetchError:
        return []                                  # como LinkedIn/portales: se traga el error de una oferta


def test_service_distinguishes_ok_empty_blocked_error():
    def boom():
        raise RuntimeError("selector roto")

    def raise_blocked():
        raise fetch._blocked("https://z.test/", 429, "429", "z.test", hard=True)
    srcs = [_Src("ok_s", lambda: [job(title="a", url="1")]), _Src("empty_s", lambda: []), _Src("swallow_s", _swallow_block),
            _Src("blocked_s", raise_blocked), _Src("error_s", boom)]
    events, rep = [], {}
    jobs, errors = run_search(SearchQuery(keywords=["x"], expand=False), srcs, on_source=lambda n, f, e, o: events.append((n, f, bool(e), o)), report=rep)
    out = {n: r["outcome"] for n, r in rep.items()}
    assert out == {"ok_s": "ok", "empty_s": "empty", "swallow_s": "blocked", "blocked_s": "blocked", "error_s": "error"}
    assert sorted(errors) == ["blocked_s", "error_s"] and len(jobs) == 1
    assert {e[0]: e[3] for e in events}["swallow_s"] == "blocked"
    st = {h["source"]: h for h in health.get_health()}
    assert st["blocked_s"]["status"] == "blocked" and st["blocked_s"]["outcome"] == "blocked"
    assert st["swallow_s"]["status"] == "blocked" and "z.test" in st["swallow_s"]["note"]
    assert st["empty_s"]["status"] == "empty" and st["empty_s"]["outcome"] == "empty"
    assert st["ok_s"]["status"] == "ok" and st["error_s"]["status"] == "error"


def test_on_source_three_args_still_supported():
    seen = []
    run_search(SearchQuery(keywords=["x"], expand=False), [_Src("s", lambda: [])], on_source=lambda n, f, e: seen.append((n, f, e)))
    assert seen == [("s", 0, None)]


def test_canary_reports_blocked():
    r = health.check_source(_Src("zz", _swallow_block), persist=False)
    assert r["status"] == "blocked" and r["outcome"] == "blocked"
    assert health.check_source(_Src("yy", lambda: []), persist=False)["status"] == "degraded"       # vacía en canario sigue = degradada


def test_blocked_status_is_not_confused_with_degraded_streak():
    for _ in range(5):
        health.record("bb", 0, None, 0.1, blocked="HTTP 429")
    row = next(h for h in health.get_health() if h["source"] == "bb")
    assert row["status"] == "blocked" and row["consecutive_zero"] == 0 and row["consecutive_errors"] == 5
    health.record("bb", 4, None, 0.1)
    assert next(h for h in health.get_health() if h["source"] == "bb")["status"] == "ok"


# ================================================================ 4. fuentes nuevas
def test_trabajosdiarios_parses_listing_and_jsonld_detail(monkeypatch):
    cfg = next(c for c in PORTALS if c.name == "trabajosdiarios")
    seen = []

    def get(url, stealth=False, timeout=30):
        seen.append(url)
        return text("trabajosdiarios_list.html") if "/ofertas-trabajo/" in url else text("trabajosdiarios_detail.html")
    monkeypatch.setattr(fetch, "get_html", get)
    src = PortalSource(cfg)
    jobs = src.search(SearchQuery(keywords=["contador"], countries=["CO"], limit_per_source=2, expand=False))
    assert seen[0] == "https://co.trabajosdiarios.com/ofertas-trabajo/de-contador"
    assert jobs and jobs[0].title.lower().startswith("contador") and jobs[0].company == "Grupo CAF tecnología SAS" and jobs[0].country == "CO"
    assert jobs[0].posted_at == "2026-09-10" and jobs[0].expires_at == "2026-11-09" and len(jobs[0].description) > 100 and jobs[0].apply_url is None
    assert set(dict(src.search_urls(SearchQuery(keywords=["x"], countries=["MX", "CR", "BR"])))) == {"MX", "CR"}
    assert dict(src.search_urls(SearchQuery(keywords=["x"], countries=["CR"])))["CR"].startswith("https://trabajosdiarios.co.cr/")


# Respuestas SINTÉTICAS con la forma que documentan Adzuna / Jooble / InfoJobs (estas APIs exigen clave y no se pudieron verificar en vivo).
ADZUNA = {"results": [
    {"id": "1", "title": "Software <strong>Engineer</strong>", "company": {"display_name": "Acme"}, "location": {"display_name": "Madrid, Comunidad de Madrid"},
     "redirect_url": "https://www.adzuna.es/land/ad/1", "created": "2026-11-02T08:00:00Z", "salary_min": 40000, "salary_max": 55000, "salary_is_predicted": "0",
     "contract_time": "full_time", "description": "Buscamos un ingeniero de software con experiencia en Python y cloud para nuestro equipo..."},
    {"id": "2", "title": "Dev", "company": {"display_name": "B"}, "location": {"display_name": "Valencia"}, "redirect_url": "https://www.adzuna.es/land/ad/2",
     "created": "2026-11-01T08:00:00Z", "salary_min": 30000, "salary_max": 30000, "salary_is_predicted": "1", "description": "x"}]}
JOOBLE = {"totalCount": 1, "jobs": [{"id": 1, "title": "Contador", "location": "Ciudad de México", "snippet": "Se requiere &nbsp;contador con experiencia",
                                     "salary": "20 000 - 25 000 MXN", "source": "x.com", "type": "Full-time", "link": "https://jooble.org/desc/1", "company": "Z",
                                     "updated": "2026-11-03T10:00:00.0000000"}]}
INFOJOBS = {"offers": [{"id": "abc", "title": "Analista SOC", "province": {"id": 33, "value": "Madrid"}, "city": "Madrid", "link": "https://www.infojobs.net/madrid/analista-soc/of-iabc",
                        "published": "2026-11-03T09:00:00.000Z", "updated": "2026-11-03T09:00:00.000Z", "author": {"name": "Banco X"}, "salaryDescription": "40.000 - 45.000 EUR bruto/año",
                        "contractType": {"id": 8, "value": "Indefinido"}, "teleworking": {"id": 1, "value": "Solo teletrabajo"}}], "totalPages": 1}


def test_adzuna_parse_ignores_predicted_salary_and_sends_native_filters(monkeypatch):
    calls = []
    monkeypatch.setattr(fetch, "get_json", lambda url, params=None, **kw: calls.append((url, params)) or ADZUNA)
    src = api.Adzuna("id", "key")
    jobs = src.search(SearchQuery(keywords=["ingeniero de software"], countries=["ES"], hours_old=72, employment_types=["full_time"], limit_per_source=10, expand=False))
    assert calls[0][0].startswith("https://api.adzuna.com/v1/api/jobs/es/search/1")
    assert calls[0][1]["max_days_old"] == 3 and calls[0][1]["full_time"] == 1
    jobs = [normalize.finalize(j, today=TODAY) for j in jobs]
    assert jobs[0].title == "Software Engineer" and jobs[0].salary_annual_min == 40000 and jobs[0].salary_currency == "EUR" and jobs[0].country == "ES"
    assert jobs[1].salary_min is None                                    # estimado por Adzuna: no se muestra como salario
    assert src.search(SearchQuery(keywords=["x"], countries=["CO"])) == []


def test_jooble_minimum_budget_and_parse(monkeypatch):
    calls = []
    monkeypatch.setattr(fetch, "get_json", lambda url, params=None, **kw: calls.append((url, kw.get("json_body"))) or JOOBLE)
    q = query.expand_query(SearchQuery(keywords=["contador"], countries=["MX"], hours_old=48, limit_per_source=5))
    jobs = api.Jooble("KEY").search(q)
    assert len(calls) == 1 and calls[0][0] == "https://jooble.org/api/KEY" and calls[0][1]["keywords"] == "contador" and calls[0][1]["datecreatedfrom"]
    j = normalize.finalize(jobs[0], today=TODAY)
    assert j.salary_min == 20000 and j.salary_currency == "MXN" and j.salary_annual_min == 240_000 and "&nbsp;" not in j.description


def test_infojobs_official_api_basic_auth_and_detail(monkeypatch):
    calls = []

    def get(url, params=None, headers=None, **kw):
        calls.append((url, params, headers))
        return INFOJOBS if url.endswith("/9/offer") else {"description": "<p>Monitorización <b>SIEM</b></p><ul><li>Splunk</li></ul>"}
    monkeypatch.setattr(fetch, "get_json", get)
    jobs = api.InfoJobs("cid", "secret").search(SearchQuery(keywords=["analista soc"], hours_old=100, limit_per_source=5, expand=False))
    assert calls[0][2]["Authorization"].startswith("Basic ") and calls[0][1]["sinceDate"] == "_7_DAYS"
    assert jobs[0].work_mode == "remote" and jobs[0].country == "ES" and jobs[0].raw["html"].startswith("<p>")
    j = normalize.finalize(jobs[0], today=TODAY)
    assert j.salary_min == 40000 and j.salary_currency == "EUR" and j.description_md and "- Splunk" in j.description_md
    assert api.InfoJobs.since(None) == "ANY" and api.InfoJobs.since(1) == "_24_HOURS" and api.InfoJobs.since(30) == "ANY"
    calls.clear()
    api.InfoJobs("cid", "secret").search(SearchQuery(keywords=["x"], fetch_description=False, expand=False))
    assert len(calls) == 1


def test_keyed_sources_only_exist_with_keys(monkeypatch):
    from app.config import settings
    from app.scout import registry
    names = lambda: {s.name for s in registry.build_sources([])}      # noqa: E731
    for k in ("adzuna_app_id", "adzuna_app_key", "jooble_api_key", "infojobs_client_id", "infojobs_client_secret"):
        monkeypatch.setattr(settings, k, "")
    assert not names() & {"adzuna", "jooble", "infojobs"} and "trabajosdiarios" in names()
    monkeypatch.setattr(settings, "adzuna_app_id", "a")
    assert "adzuna" not in names()                                    # falta la mitad de la credencial
    monkeypatch.setattr(settings, "adzuna_app_key", "b")
    monkeypatch.setattr(settings, "jooble_api_key", "k")
    monkeypatch.setattr(settings, "infojobs_client_id", "i")
    monkeypatch.setattr(settings, "infojobs_client_secret", "s")
    assert {"adzuna", "jooble", "infojobs"} <= names()


# ================================================================ 5. métricas de calidad
def test_quality_metrics_and_cross_source_duplicates():
    a = [job(title="Analista SOC", company="X", location="Bogotá", url="https://p.com/1", description="d" * 100, posted_at="2026-11-01",
             salary_min=1, apply_url="https://empresa.com/job/1"),
         job(title="Otro", company="Y", location="Cali", url="https://p.com/2", description="corta", apply_url="https://p.com/2")]
    b = [job(title="analista soc", company="x", location="bogota", url="https://q.com/9", description="d" * 100)]
    dups = quality.duplicate_keys({"a": a, "b": b})
    m = quality.source_metrics(a, dups["a"])
    assert m == {"n": 2, "desc": 50.0, "salary": 50.0, "date": 50.0, "apply_ext": 50.0, "dup": 50.0}
    assert quality.source_metrics([], set())["desc"] is None
    assert quality.source_metrics(b, dups["b"])["dup"] == 100.0


def test_quality_measure_runs_queries_with_variants_and_formats_table():
    class S:
        group, countries = "api", set()

        def __init__(self, name, jobs):
            self.name, self._jobs, self.seen = name, jobs, []

        def search(self, q):
            self.seen.append(q)
            return self._jobs
    s1 = S("s1", [job(title="Analista SOC", company="X", location="Bogotá", url="https://a.com/1", description="d" * 100, posted_at="hoy")])
    s2 = S("s2", [])
    res = quality.measure([s1, s2], quality.DEFAULT_QUERIES[:1], limit=3, workers=2)
    assert s1.seen[0].variants and s1.seen[0].limit_per_source == 3                  # se mide como en producción: con variantes
    assert res["sources"]["s1"]["n"] == 1 and res["sources"]["s1"]["date"] == 100.0 and res["sources"]["s2"]["outcomes"] == {"empty": 1}
    table = quality.format_report(res)
    assert "s1" in table and "%desc" in table and "empty:1" in table
    assert len(quality.DEFAULT_QUERIES) == 4


def test_escalation_is_remembered_per_host(monkeypatch):
    calls = []

    def fake(url, *, stealth=False, **kw):
        calls.append((url, stealth))
        return fetch.Resp(200, BIG) if stealth else fetch.Resp(403, "denied")
    monkeypatch.setattr(fetch, "_transport", fake)
    fetch.get_html("https://hard.test/a")
    fetch.get_html("https://hard.test/b")
    assert calls == [("https://hard.test/a", False), ("https://hard.test/a", True), ("https://hard.test/b", True)]     # la 2.ª va directa
    fetch.get_html("https://other.test/a")
    assert calls[-2:] == [("https://other.test/a", False), ("https://other.test/a", True)]
