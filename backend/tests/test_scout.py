import json

from app.domain.job import Job, SearchQuery
from app.scout import fetch
from app.scout.jsonld import extract_jobs
from app.scout.service import run_search
from app.scout.sources.careers import CareerPage
from app.scout.sources.portals import PortalConfig, PortalSource

POSTING = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Analista SOC",
           "description": "<p>Monitoreo <b>SIEM</b> y respuesta a incidentes</p>", "datePosted": "2026-10-01",
           "hiringOrganization": {"@type": "Organization", "name": "Bancolombia"},
           "jobLocation": {"@type": "Place", "address": {"addressLocality": "Medellín", "addressCountry": "CO"}},
           "baseSalary": {"currency": "COP", "value": {"minValue": 5000000, "maxValue": 7000000}}}


def page(data) -> str:
    return f'<html><script type="application/ld+json">{json.dumps(data)}</script></html>'


def test_jsonld_extraction():
    [j] = extract_jobs(page(POSTING), "https://x/job/1", "t")
    assert (j.title, j.company, j.location) == ("Analista SOC", "Bancolombia", "Medellín, CO")
    assert "SIEM" in j.description and "<" not in j.description
    assert j.salary == "5000000-7000000 COP"


def test_jsonld_graph_and_garbage():
    html = page({"@graph": [{"@type": "WebSite"}, POSTING]}) + '<script type="application/ld+json">{bad</script>'
    assert len(extract_jobs(html, "u", "t")) == 1


def test_portal_follows_links_and_reads_detail(monkeypatch):
    pages = {"https://p.test/s?q=soc": '<a class="o" href="/o/1">x</a><a class="o" href="/o/2">y</a>',
             "https://p.test/o/1": page(POSTING), "https://p.test/o/2": "<html>sin json-ld</html>"}
    monkeypatch.setattr(fetch, "get_html", lambda url, stealth=False, timeout=30: pages[url])
    src = PortalSource(PortalConfig("p", set(), "https://p.test/s?q={kw}", "a.o"))
    jobs = src.search(SearchQuery(keywords=["soc"]))
    assert [j.title for j in jobs] == ["Analista SOC"]


def test_career_page_discovers_job_links(monkeypatch):
    pages = {"https://c.test/careers": '<a href="/jobs/123">v</a><a href="/about">a</a>', "https://c.test/jobs/123": page({**POSTING, "hiringOrganization": None})}
    monkeypatch.setattr(fetch, "get_html", lambda url, stealth=False, timeout=30: pages[url])
    [j] = CareerPage("MercadoLibre", "https://c.test/careers").search(SearchQuery(keywords=["soc"]))
    assert j.company == "MercadoLibre"


def test_service_dedups_and_isolates_failures():
    class Ok:
        name, countries = "ok", set()
        def search(self, q): return [Job(title="Analista SOC", company="X", location="Bogotá", url="u1", source="ok")]
    class Dup(Ok):
        name = "dup"
        def search(self, q): return [Job(title="analista soc", company="x", location="bogotá", url="u2", source="dup")]
    class Boom(Ok):
        name = "boom"
        def search(self, q): raise RuntimeError("bloqueado")
    jobs, errors = run_search(SearchQuery(keywords=["soc"]), [Ok(), Dup(), Boom()])
    assert len(jobs) == 1 and errors == {"boom": "bloqueado"}


def test_jsonld_tolerates_list_fields():
    p = {**POSTING, "jobLocation": {"address": {"addressLocality": ["Cali", "Bogotá"], "addressCountry": {"name": "CO"}}},
         "hiringOrganization": [{"name": "Ara"}]}
    [j] = extract_jobs(page(p), "u", "t")
    assert j.location == "Cali, Bogotá, CO" and j.company == "Ara"


def test_career_fallback_uses_h2_and_skips_scripts(monkeypatch):
    detail = "<html><body><script>var x=1;</script><h2>Careers</h2><h2>Analista de Ruteo</h2><p>Optimiza rutas</p></body></html>"
    pages = {"https://c.test/en/positions": '<a href="/en/positions?id=9">v</a>', "https://c.test/en/positions?id=9": detail}
    monkeypatch.setattr(fetch, "get_html", lambda url, stealth=False, timeout=30: pages[url])
    [j] = CareerPage("Meli", "https://c.test/en/positions").search(SearchQuery(keywords=["x"]))
    assert j.title == "Analista de Ruteo" and "var x" not in j.description and "Optimiza" in j.description
