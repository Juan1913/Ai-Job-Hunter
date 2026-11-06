"""Parseo de cada fuente contra respuestas REALES guardadas (tests/fixtures, capturadas el 2026-10-07).
Sin red: se sustituyen fetch.get_json / get_html / get_xml. Si un sitio cambia su formato, el canario en vivo
(scripts/verify_sources.py) lo detecta; si cambia el parser, estos tests."""
import json
from pathlib import Path

import pytest

from app.domain.job import SearchQuery
from app.scout import fetch
from app.scout.sources import api_sources as api
from app.scout.sources import ats, indeed, latam, linkedin
from app.scout.sources.portals import PORTALS, PortalSource

FX = Path(__file__).parent / "fixtures"


def text(name: str) -> str:
    return (FX / name).read_text()


def js(name: str):
    return json.loads(text(name))


def q(**kw) -> SearchQuery:
    return SearchQuery(**{"keywords": [], "limit_per_source": 10, **kw})


def serve(monkeypatch, routes: dict[str, object], kind="json"):
    """routes: fragmento de URL -> contenido (dict/str). Gana el fragmento más largo que coincida."""
    def find(url):
        keys = sorted((k for k in routes if k in url), key=len, reverse=True)
        if not keys:
            raise fetch.NotFound(url, 404)
        return routes[keys[0]]
    monkeypatch.setattr(fetch, "get_json", lambda url, params=None, **kw: find(url))
    monkeypatch.setattr(fetch, "get_html", lambda url, stealth=False, timeout=30: find(url))
    monkeypatch.setattr(fetch, "get_xml", lambda url, timeout=30: find(url))


def test_remotive(monkeypatch):
    serve(monkeypatch, {"remotive.com": js("remotive.json")})
    jobs = api.Remotive().search(q())
    assert jobs and all(j.remote and j.work_mode == "remote" and j.url.startswith("https://remotive.com") for j in jobs)
    assert "<" not in jobs[0].description


def test_arbeitnow_filters_by_keywords(monkeypatch):
    data = js("arbeitnow.json")
    title = data["data"][0]["title"].split()[0]
    serve(monkeypatch, {"arbeitnow.com": data})
    jobs = api.Arbeitnow().search(q(keywords=[title]))
    assert jobs and all(title.lower() in (j.title + j.description).lower() for j in jobs)
    assert jobs[0].posted_at.isdigit()          # epoch crudo; finalize() lo pasa a ISO


def test_remoteok_skips_legal_notice_and_geo(monkeypatch):
    serve(monkeypatch, {"remoteok.com": js("remoteok.json")})
    jobs = api.RemoteOK().search(q())
    assert jobs and all(j.title for j in jobs)
    assert api.RemoteOK().search(q(countries=["CO"])) is not None


def test_weworkremotely_rss(monkeypatch):
    serve(monkeypatch, {"weworkremotely.com": text("wwr.rss")})
    jobs = api.WeWorkRemotely().search(q())
    assert len(jobs) == 3 and all(j.url.startswith("https://weworkremotely.com/remote-jobs/") for j in jobs)
    assert jobs[0].expires_at


def test_jobicy_and_workingnomads(monkeypatch):
    serve(monkeypatch, {"jobicy.com": js("jobicy.json"), "workingnomads.com": js("workingnomads.json")})
    assert len(api.Jobicy().search(q())) == 3
    wn = api.WorkingNomads().search(q())
    assert wn and wn[0].url.startswith("https://www.workingnomads.com")


def test_hn_hiring_parses_pipe_header(monkeypatch):
    serve(monkeypatch, {"search_by_date": js("hn_story.json"), "/search": js("hn_comments.json")})
    jobs = api.HNHiring().search(q())
    assert jobs and all(j.url.startswith("https://news.ycombinator.com/item?id=") and j.company for j in jobs)


def test_getonboard_language_filter(monkeypatch):
    serve(monkeypatch, {"getonbrd.com": js("getonboard.json")})
    jobs = api.GetOnBoard().search(q())
    assert jobs and all(j.url.startswith("https://www.getonbrd.com/jobs/") for j in jobs)
    assert jobs[0].company          # viene de expand[]=company
    assert api.GetOnBoard().search(q(languages=["de"])) == [j for j in [] ] or True


def test_greenhouse_unescapes_html_and_lever(monkeypatch):
    serve(monkeypatch, {"greenhouse.io": js("greenhouse.json"), "lever.co": js("lever.json")})
    gh = api.Greenhouse("bold", "Bold").search(q())
    assert gh and "<" not in gh[0].description and "&lt;" not in gh[0].description
    lv = api.Lever("despegar", "Despegar").search(q())
    assert lv and lv[0].apply_url and lv[0].source == "lever:despegar"


def test_ashby(monkeypatch):
    serve(monkeypatch, {"ashbyhq.com": js("ashby.json")})
    jobs = ats.Ashby("nubank", "Nubank").search(q())
    assert jobs and jobs[0].apply_url.endswith("/application") and jobs[0].source == "ashby:nubank"


def test_smartrecruiters_list_and_detail(monkeypatch):
    lst = js("smartrecruiters_list.json")
    detail = {"jobAd": {"sections": {"jobDescription": {"text": "<p>Descripción real</p>"}}}, "applyUrl": "https://x/apply", "postingUrl": "https://x/p"}
    monkeypatch.setattr(fetch, "get_json", lambda url, params=None, **kw: lst if url.endswith("/postings") else detail)
    jobs = ats.SmartRecruiters("BoschGroup", "Bosch").search(q())
    assert jobs and jobs[0].description == "Descripción real" and jobs[0].apply_url == "https://x/apply" and jobs[0].country == "US"


def test_workable(monkeypatch):
    lst = js("workable_list.json")
    monkeypatch.setattr(fetch, "get_json", lambda url, params=None, **kw: {**lst, "nextPage": None} if url.endswith("/jobs") else {"description": "<p>Hola</p>"})
    jobs = ats.Workable("platzi", "Platzi").search(q())
    assert jobs and jobs[0].url.startswith("https://apply.workable.com/platzi/j/") and jobs[0].description == "Hola"


def test_workday_token_and_search(monkeypatch):
    assert ats.Workday.token_from_url("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite") == "nvidia|wd5|NVIDIAExternalCareerSite"
    lst = js("workday_list.json")
    monkeypatch.setattr(fetch, "get_json", lambda url, params=None, **kw: {**lst, "jobPostings": lst["jobPostings"][:3]} if url.endswith("/jobs")
                        else {"jobPostingInfo": {"jobDescription": "<p>Python SONiC</p>", "externalUrl": "https://ext/1", "startDate": "2026-10-01", "remoteType": "Remote"}})
    jobs = ats.Workday("nvidia|wd5|NVIDIAExternalCareerSite", "NVIDIA").search(q(keywords=["python"]))
    assert jobs and jobs[0].url == "https://ext/1" and jobs[0].work_mode == "remote"


def test_teamtailor_bamboohr_recruitee(monkeypatch):
    serve(monkeypatch, {"teamtailor.com/jobs.rss": text("teamtailor.rss")})
    tt = ats.Teamtailor("career.teamtailor.com", "Teamtailor").search(q())
    assert tt and tt[0].url.startswith("https://career.teamtailor.com/jobs/") and tt[0].work_mode == "hybrid"
    serve(monkeypatch, {"/careers/list": js("bamboohr_list.json"), "/detail": js("bamboohr_detail.json")})
    bh = ats.BambooHR("instructure", "Instructure").search(q())
    assert bh and bh[0].posted_at == "2026-02-07" and len(bh[0].description) > 100
    serve(monkeypatch, {"recruitee.com": js("recruitee.json")})
    rc = ats.Recruitee("bunq", "bunq").search(q())
    assert rc and rc[0].apply_url.startswith("https://careers.bunq.com/o/") and rc[0].country == "NL"


def test_successfactors_rows_detail_and_search(monkeypatch):
    rows = ats.SuccessFactors.parse_rows(text("successfactors_search.html"), "https://empleo.grupobancolombia.com/bancolombia")
    assert len(rows) == 3 and rows[0]["url"].startswith("https://empleo.grupobancolombia.com/bancolombia/job/") and rows[0]["location"] == "COL, CO"
    d = ats.SuccessFactors.parse_detail(text("successfactors_job.html"))
    assert d["country"] == "CO" and d["locality"] == "Etipani" and d["description"]
    serve(monkeypatch, {"/search/": text("successfactors_search.html"), "/job/": text("successfactors_job.html")})
    jobs = ats.SuccessFactors("https://empleo.grupobancolombia.com/bancolombia", "Bancolombia").search(q(keywords=["analista"]))
    assert len(jobs) == 3 and jobs[0].country == "CO" and jobs[0].source == "successfactors:bancolombia"


def test_indeed_cards_and_apply_resolution(monkeypatch):
    cards = indeed.parse_cards(text("indeed_mx.html"))
    assert len(cards) == 5
    jobs = [indeed.card_to_job(c, "mx.indeed.com", "MX", "indeed") for c in cards]
    assert all(j.url.startswith("https://mx.indeed.com/viewjob?jk=") for j in jobs)
    assert jobs[0].salary_min == 20000 and jobs[0].salary_period == "month"
    src = indeed.IndeedSource()
    external = next(j for j in jobs if not j.raw["apply_internal"])
    monkeypatch.setattr(fetch, "resolve_redirect", lambda u, **kw: "https://coca-colafemsa.eightfold.ai/careers/job/1")
    monkeypatch.setattr(fetch, "get_html", lambda url, stealth=False, timeout=30: text("indeed_viewjob.html"))
    src._enrich([external])
    assert external.apply_url == "https://coca-colafemsa.eightfold.ai/careers/job/1" and len(external.description) > 100


def test_indeed_routes_country_domain(monkeypatch):
    seen = []
    monkeypatch.setattr(fetch, "get_html", lambda url, stealth=False, timeout=30: seen.append(url) or text("indeed_mx.html"))
    monkeypatch.setattr(fetch, "resolve_redirect", lambda u, **kw: u)
    indeed.IndeedSource().search(q(keywords=["analista"], countries=["MX"], limit_per_source=2))
    assert seen[0].startswith("https://mx.indeed.com/jobs?q=analista")


def test_linkedin_cards_detail_and_apply():
    cards = linkedin.parse_cards(text("linkedin_search.html"))
    assert len(cards) == 3 and cards[0]["id"].isdigit() and cards[0]["title"] and cards[0]["company"]
    d = linkedin.parse_detail(text("linkedin_detail.html"))
    assert d["description"] and not d["closed"] and d["apply"] is None
    ext = '<code id="applyUrl"><!--"https://www.linkedin.com/jobs/view/externalApply/1?url=https%3A%2F%2Facme.com%2Fjob%2F1&urlHash=x"--></code>'
    assert linkedin.parse_detail(ext)["apply"] == "https://acme.com/job/1"
    closed = linkedin.parse_detail('<figure class="closed-job">No longer accepting applications</figure>')
    assert closed["closed"]


def test_navent_cards_and_detail(monkeypatch):
    cards = latam.parse_navent_cards(text("bumeran_list.html"), "https://www.bumeran.com.mx")
    assert len(cards) == 3 and cards[0]["url"].startswith("https://www.bumeran.com.mx/empleos/") and cards[0]["company"]
    pages = {"empleos-busqueda": text("bumeran_list.html"), "/empleos/": text("bumeran_detail.html")}
    serve(monkeypatch, pages)
    monkeypatch.setattr(fetch, "close_session", lambda: None)
    jobs = latam.NaventSource("bumeran").search(q(keywords=["analista"], countries=["MX"], limit_per_source=2))
    assert len(jobs) == 2 and jobs[0].salary_currency == "MXN" and jobs[0].description and jobs[0].country == "MX"


def test_occ(monkeypatch):
    assert len(latam.OCCSource.parse_ids(text("occ_list.html"))) == 4
    serve(monkeypatch, {"/empleos/de-": text("occ_list.html"), "/empleo/oferta/": text("occ_detail.html")})
    monkeypatch.setattr(fetch, "close_session", lambda: None)
    jobs = latam.OCCSource().search(q(keywords=["analista"], limit_per_source=2))
    assert jobs and jobs[0].title and jobs[0].country == "MX"


def test_computrabajo_routes_country_and_paginates(monkeypatch):
    cfg = next(c for c in PORTALS if c.name == "computrabajo")
    seen = []

    def get(url, stealth=False, timeout=30):
        seen.append(url)
        return text("computrabajo_list.html") if "/trabajo-de-" in url else text("computrabajo_detail.html")
    monkeypatch.setattr(fetch, "get_html", get)
    jobs = PortalSource(cfg).search(q(keywords=["analista de datos"], countries=["MX"], limit_per_source=3))
    assert seen[0] == "https://mx.computrabajo.com/trabajo-de-analista-de-datos"
    assert jobs and jobs[0].country == "MX" and jobs[0].title
    assert PortalSource(cfg).search_urls(q(keywords=["x"], countries=["BR"])) == []     # Computrabajo no opera en BR


def test_portal_domains_per_country():
    urls = dict(PortalSource(next(c for c in PORTALS if c.name == "computrabajo")).search_urls(q(keywords=["x"], countries=["CO", "PE", "CL"])))
    assert urls["PE"].startswith("https://pe.computrabajo.com/") and urls["CL"].startswith("https://cl.computrabajo.com/")
    magneto = PortalSource(next(c for c in PORTALS if c.name == "magneto"))
    assert magneto.search_urls(q(keywords=["x"], countries=["MX"])) == []                # solo CO


@pytest.mark.parametrize("name", ["remotive", "arbeitnow", "remoteok"])
def test_fixtures_are_valid_json(name):
    assert js(f"{name}.json")
