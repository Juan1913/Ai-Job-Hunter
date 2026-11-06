"""ATS de empresas con API/feeds públicos (sin login). Cada clase = un tablero de una empresa.
Greenhouse y Lever están en api_sources.py. Verificadas en vivo el 2026-10-07:
Ashby (nubank, addi, belvo), SmartRecruiters (endava), Workable (platzi), Workday cxs (nvidia como tenant de prueba),
Teamtailor (career.teamtailor.com), BambooHR (instructure), Recruitee (bunq), SuccessFactors RMK (Bancolombia)."""
import re
from urllib.parse import quote, urljoin, urlsplit

from scrapling.parser import Selector

from app.domain.job import Job, SearchQuery
from app.scout import fetch
from app.scout.base import cap, filter_company_jobs
from app.scout.normalize import clean_html, normalize_employment
from app.scout.sources.api_sources import _txt, parse_xml


class _Board:
    """Base de un tablero de empresa: nombre estable `kind:token`, sin país fijo, cero resultados es normal."""
    kind = "ats"
    countries: set[str] = set()
    group, zero_ok = "ats", True

    def __init__(self, token: str, company: str):
        self.token, self.company, self.name = token, company, f"{self.kind}:{token}"


def _mode(workplace: str | None, remote: bool | None = None, hybrid: bool | None = None) -> str | None:
    w = (workplace or "").lower().replace("-", "").replace("_", "")
    if hybrid or w == "hybrid":
        return "hybrid"
    if remote or w == "remote":
        return "remote"
    if w in ("onsite", "office"):
        return "onsite"
    return None


# ---------------------------------------------------------------- Ashby
class Ashby(_Board):
    """api.ashbyhq.com/posting-api/job-board/{token}"""
    kind = "ashby"

    def search(self, q: SearchQuery) -> list[Job]:
        data = fetch.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{self.token}", params={"includeCompensation": "true"})
        out = []
        for j in data.get("jobs", []):
            if j.get("isListed") is False:
                continue
            places = [j.get("location", ""), *[s.get("location", "") for s in j.get("secondaryLocations") or []]]
            comp = (j.get("compensation") or {}).get("compensationTierSummary")
            out.append(Job(title=j["title"].strip(), company=self.company, location=", ".join(p for p in dict.fromkeys(places) if p),
                           remote=bool(j.get("isRemote")), work_mode=_mode(j.get("workplaceType"), j.get("isRemote")),
                           description=j.get("descriptionPlain") or clean_html(j.get("descriptionHtml", "")), raw={"html": j.get("descriptionHtml", "")},
                           url=j["jobUrl"], source=self.name,
                           posted_at=j.get("publishedAt"), apply_url=j.get("applyUrl"), salary=comp,
                           employment_type=normalize_employment(j.get("employmentType"))))
        return filter_company_jobs(out, q)


# ---------------------------------------------------------------- SmartRecruiters
class SmartRecruiters(_Board):
    """api.smartrecruiters.com/v1/companies/{id}/postings (filtros q y country en el servidor; detalle por oferta)."""
    kind = "smartrecruiters"
    API = "https://api.smartrecruiters.com/v1/companies"

    def search(self, q: SearchQuery) -> list[Job]:
        params = {"limit": 100, "q": " ".join(q.keywords)}
        countries = [c.lower() for c in q.countries] or [None]
        rows: list[dict] = []
        for c in countries:
            data = fetch.get_json(f"{self.API}/{self.token}/postings", params={**params, **({"country": c} if c else {})})
            rows += data.get("content", [])
        out = []
        for r in rows[: cap(q)]:
            loc = r.get("location") or {}
            desc, apply, html = "", None, ""
            try:
                d = fetch.get_json(f"{self.API}/{self.token}/postings/{r['id']}")
                sections = (d.get("jobAd") or {}).get("sections") or {}
                desc = clean_html(" ".join((s or {}).get("text", "") for s in sections.values()))
                html = "".join(f"<h3>{(s or {}).get('title', '')}</h3>{(s or {}).get('text', '')}" if (s or {}).get("title") else (s or {}).get("text", "")
                               for s in sections.values())
                apply = d.get("applyUrl")
                url = d.get("postingUrl") or f"https://jobs.smartrecruiters.com/{self.token}/{r['id']}"
            except fetch.FetchError:
                url = f"https://jobs.smartrecruiters.com/{self.token}/{r['id']}"
            out.append(Job(title=r["name"], company=(r.get("company") or {}).get("name") or self.company,
                           location=loc.get("fullLocation") or ", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country", "").upper()) if x),
                           remote=bool(loc.get("remote")), work_mode=_mode(None, loc.get("remote"), loc.get("hybrid")), description=desc, raw={"html": html}, url=url,
                           source=self.name, posted_at=r.get("releasedDate"), apply_url=apply, country=(loc.get("country") or "").upper() or None,
                           employment_type=normalize_employment((r.get("typeOfEmployment") or {}).get("label"))))
        return out


# ---------------------------------------------------------------- Workable
class Workable(_Board):
    """apply.workable.com/api/v3/accounts/{slug}/jobs (POST) + detalle v2."""
    kind = "workable"

    def search(self, q: SearchQuery) -> list[Job]:
        body = {"query": " ".join(q.keywords), "location": [], "department": [], "worktype": [], "remote": []}
        rows: list[dict] = []
        for _ in range(3):
            data = fetch.get_json(f"https://apply.workable.com/api/v3/accounts/{self.token}/jobs", method="POST", json_body=body)
            rows += data.get("results", [])
            if not data.get("nextPage") or len(rows) >= cap(q):
                break
            body = {**body, "token": data["nextPage"]}
        out = []
        for r in rows[: cap(q)]:
            loc = r.get("location") or {}
            code = r.get("shortcode")
            url = f"https://apply.workable.com/{self.token}/j/{code}/"
            desc, html = "", ""
            try:
                d = fetch.get_json(f"https://apply.workable.com/api/v2/accounts/{self.token}/jobs/{code}")
                desc = clean_html(" ".join(d.get(k) or "" for k in ("description", "requirements", "benefits")))
                html = "".join(f"<h3>{t}</h3>{d.get(k)}" if k != "description" and d.get(k) else (d.get(k) or "")
                               for k, t in (("description", ""), ("requirements", "Requisitos"), ("benefits", "Beneficios")))
            except fetch.FetchError:
                pass
            out.append(Job(title=r["title"], company=self.company, location=", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x),
                           remote=bool(r.get("remote")), work_mode=_mode(r.get("workplace"), r.get("remote")), description=desc, raw={"html": html}, url=url,
                           source=self.name, posted_at=r.get("published"), apply_url=url + "apply/", country=loc.get("countryCode"),
                           language=r.get("language") if r.get("language") in ("es", "en", "pt") else None))
        return out


# ---------------------------------------------------------------- Workday (cxs)
class Workday(_Board):
    """{tenant}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs. token = 'tenant|wdN|site'."""
    kind = "workday"

    def __init__(self, token: str, company: str):
        super().__init__(token, company)
        self.tenant, self.wd, self.site = token.split("|")
        self.base = f"https://{self.tenant}.{self.wd}.myworkdayjobs.com"

    @staticmethod
    def token_from_url(url: str) -> str | None:
        p = urlsplit(url)
        m = re.match(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com$", (p.hostname or "").lower())
        parts = [x for x in p.path.split("/") if x and not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", x)]
        return f"{m.group(1)}|{m.group(2)}|{parts[0]}" if m and parts else None

    def search(self, q: SearchQuery) -> list[Job]:
        api = f"{self.base}/wday/cxs/{self.tenant}/{self.site}"
        rows: list[dict] = []
        offset = 0
        while len(rows) < cap(q) and offset < 100:
            data = fetch.get_json(f"{api}/jobs", method="POST", json_body={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": " ".join(q.keywords)})
            page = data.get("jobPostings", [])
            rows += page
            offset += 20
            if len(page) < 20:
                break
        out = []
        for r in rows[: cap(q)]:
            path = r.get("externalPath", "")
            url = f"{self.base}/{self.site}{path}"
            desc, html, posted, mode, emp = "", "", r.get("postedOn"), None, None
            try:
                info = fetch.get_json(f"{api}{path}").get("jobPostingInfo", {})
                desc, html = clean_html(info.get("jobDescription", "")), info.get("jobDescription", "")
                posted = info.get("startDate") or posted
                mode = _mode(info.get("remoteType"))
                emp = normalize_employment(info.get("timeType"))
                url = info.get("externalUrl") or url
            except fetch.FetchError:
                pass
            out.append(Job(title=r["title"], company=self.company, location=r.get("locationsText", ""), description=desc, raw={"html": html}, url=url,
                           source=self.name, posted_at=posted, apply_url=url, work_mode=mode, employment_type=emp))
        return out


# ---------------------------------------------------------------- Teamtailor (RSS)
class Teamtailor(_Board):
    """{host}/jobs.rss — host = subdominio .teamtailor.com o el dominio propio de carreras. token = host."""
    kind = "teamtailor"

    def search(self, q: SearchQuery) -> list[Job]:
        root = parse_xml(fetch.get_xml(f"https://{self.token}/jobs.rss"))
        out = []
        for it in root.iter("item"):
            loc = it.find("{*}locations/{*}location")
            place = ", ".join(x for x in ((_txt(loc, "{*}city"), _txt(loc, "{*}country")) if loc is not None else ()) if x)
            out.append(Job(title=_txt(it, "title"), company=self.company, location=place, description=clean_html(_txt(it, "description")),
                           raw={"html": _txt(it, "description")}, url=_txt(it, "link"), source=self.name, posted_at=_txt(it, "pubDate"),
                           work_mode=_mode(_txt(it, "remoteStatus"))))
        return filter_company_jobs([j for j in out if j.title], q)


# ---------------------------------------------------------------- BambooHR
class BambooHR(_Board):
    """{sub}.bamboohr.com/careers/list (+ /careers/{id}/detail)."""
    kind = "bamboohr"

    def search(self, q: SearchQuery) -> list[Job]:
        base = f"https://{self.token}.bamboohr.com/careers"
        rows = fetch.get_json(f"{base}/list").get("result", [])
        out = []
        for r in rows:
            loc = r.get("location") or {}
            out.append(Job(title=r["jobOpeningName"], company=self.company, location=", ".join(x for x in (loc.get("city"), loc.get("state")) if x),
                           remote=bool(r.get("isRemote")), work_mode="remote" if r.get("isRemote") else None,
                           url=f"{base}/{r['id']}", source=self.name, apply_url=f"{base}/{r['id']}",
                           employment_type=normalize_employment(r.get("employmentStatusLabel")), description=r.get("departmentLabel") or ""))
        out = filter_company_jobs(out, q)
        for j in out:                       # detalle solo para las relevantes
            try:
                info = fetch.get_json(f"{j.url}/detail").get("result", {}).get("jobOpening", {})
                j.description = clean_html(info.get("description", "")) or j.description
                j.raw["html"] = info.get("description", "")
                j.posted_at = info.get("datePosted")
            except fetch.FetchError:
                pass
        return out


# ---------------------------------------------------------------- Recruitee
class Recruitee(_Board):
    """{sub}.recruitee.com/api/offers/"""
    kind = "recruitee"

    def search(self, q: SearchQuery) -> list[Job]:
        out = []
        for o in fetch.get_json(f"https://{self.token}.recruitee.com/api/offers/").get("offers", []):
            if o.get("status") not in (None, "published"):
                continue
            sal = o.get("salary") or {}
            extra = {"salary_min": float(sal["min"]), "salary_max": float(sal.get("max") or sal["min"]), "salary_currency": sal.get("currency"),
                     "salary_period": sal.get("period")} if sal.get("min") else {}
            out.append(Job(title=o["title"], company=o.get("company_name") or self.company, location=o.get("location", ""),
                           remote=bool(o.get("remote")), work_mode=_mode(None, o.get("remote"), o.get("hybrid")) or ("onsite" if o.get("on_site") else None),
                           description=clean_html(f"{o.get('description', '')} {o.get('requirements', '')}"),
                           raw={"html": f"{o.get('description', '')}<h3>Requisitos</h3>{o.get('requirements', '')}" if o.get("requirements") else o.get("description", "")},
                           url=o["careers_url"], source=self.name, posted_at=o.get("published_at"), apply_url=o.get("careers_apply_url"),
                           country=o.get("country_code"), expires_at=o.get("close_at"),
                           employment_type=normalize_employment(o.get("employment_type_code")), **extra))
        return filter_company_jobs(out, q)


# ---------------------------------------------------------------- SAP SuccessFactors (Career Site Builder / RMK)
class SuccessFactors(_Board):
    """Bancolombia, Globant... Sitios RMK: {base}/search/?q=&startrow=N con filas `tr.data-row` y microdatos en el detalle.
    token = URL base del sitio (p. ej. https://empleo.grupobancolombia.com/bancolombia)."""
    kind = "successfactors"
    PAGE = 25

    def __init__(self, token: str, company: str):
        super().__init__(token.rstrip("/"), company)
        self.base = token.rstrip("/")
        self.name = f"successfactors:{company.lower().replace(' ', '-')}"

    @staticmethod
    def parse_rows(html: str, base: str) -> list[dict]:
        page = Selector(html)
        rows = []
        for tr in page.css("tr.data-row"):
            a = tr.css("a.jobTitle-link")
            if not a:
                continue
            rows.append({"title": " ".join((a[0].get_all_text() or "").split()), "url": urljoin(base + "/", a[0].attrib.get("href", "")),
                         "location": " ".join(" ".join(tr.css("td.colLocation .jobLocation::text").getall()).split()),
                         "date": " ".join(" ".join(tr.css("td.colDate .jobDate::text").getall()).split())})
        return rows

    @staticmethod
    def parse_detail(html: str) -> dict:
        page = Selector(html)

        def meta(prop: str) -> str:
            return (page.css(f'[itemprop="{prop}"]::attr(content)').get() or "").strip()
        desc = page.css('[itemprop="description"]')
        return {"description": clean_html(desc[0].html_content) if desc else "", "html": desc[0].html_content if desc else "", "posted": meta("datePosted"),
                "locality": meta("addressLocality"), "region": meta("addressRegion"), "country": meta("addressCountry"),
                "org": meta("hiringOrganization")}

    def search(self, q: SearchQuery) -> list[Job]:
        rows: list[dict] = []
        start = 0
        while len(rows) < cap(q) and start < 200:
            url = f"{self.base}/search/?q={quote(' '.join(q.keywords))}&startrow={start}"
            try:
                page_rows = self.parse_rows(fetch.get_html(url), self.base)
            except fetch.NotFound:
                break
            new = [r for r in page_rows if r["url"] not in {x["url"] for x in rows}]
            if not new:
                break
            rows += new
            start += max(len(page_rows), 1)
            if len(page_rows) < self.PAGE:
                break
        out = []
        for r in rows[: cap(q)]:
            try:
                d = self.parse_detail(fetch.get_html(r["url"]))
            except fetch.NotFound:
                continue                  # la vacante se cerró entre el listado y el detalle
            place = ", ".join(x for x in (d["locality"], d["region"], d["country"]) if x) or r["location"]
            out.append(Job(title=r["title"], company=self.company, location=place, description=d["description"], raw={"html": d["html"]}, url=r["url"], source=self.name,
                           posted_at=d["posted"] or r["date"], apply_url=r["url"], country=(d["country"] or "").upper() or None))
        return out
