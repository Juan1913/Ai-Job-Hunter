"""Portales LATAM renderizados en el navegador.

* Navent (Bumeran MX/AR/PE/EC/PA, Zonajobs AR, Laborum CL): SPA protegida (la API devuelve 403): el listado se
  renderiza con StealthyFetcher y cada oferta publica JSON-LD. Verificado en vivo el 2026-10-07: bumeran mx/pe,
  zonajobs ar, laborum cl.
* OCC Mundial (MX): el listado se renderiza con StealthyFetcher (ids `jobcard-<id>`), el detalle
  /empleo/oferta/{id}/ se lee con Fetcher por JSON-LD. Verificado el 2026-10-07.

No integrados (ver informe): Glassdoor (ToS + tracking), Jobomas (listado exige JS/login), Trabajando (SPA sin datos estáticos)."""
from urllib.parse import quote

from scrapling.parser import Selector

from app.domain.geo import effective_countries, portal_hosts
from app.domain.job import Job, SearchQuery
from app.scout import fetch
from app.scout.base import cap
from app.scout.jsonld import extract_jobs
from app.scout.normalize import clean_html, is_closed_text
from app.scout.query import query_terms
from app.scout.sources.portals import per_country_cap, slugify, with_param


def parse_navent_cards(html: str, base: str) -> list[dict]:
    page = Selector(html)
    out = []
    for a in page.css('a[href^="/empleos/"]'):
        title = " ".join((a.css("h2::text").get() or "").split())
        if not title:
            continue
        h3 = [" ".join(t.split()) for t in a.css("h3::text").getall() if t.strip()]
        out.append({"title": title, "url": base + a.attrib["href"], "date": h3[0] if h3 else None, "company": h3[1] if len(h3) > 1 else "",
                    "location": " ".join((a.css('i[aria-label="Ubicación"] + span h3::text').get() or "").split()),
                    "mode": " ".join((a.css('i[aria-label="Modalidad"] + span h3::text').get() or "").split())})
    return out


class NaventSource:
    """Un portal de la familia Navent (misma plataforma) en sus países."""
    group = "portal"

    def __init__(self, portal: str):
        self.portal, self.name = portal, portal
        self.countries = set(portal_hosts(portal))

    def search(self, q: SearchQuery) -> list[Job]:
        ccs = effective_countries(q.countries, self.countries)
        jobs: list[Job] = []
        try:
            for cc in ccs:
                host = portal_hosts(self.portal)[cc]
                base = f"https://{host}"
                want, cards = per_country_cap(q, len(ccs)), {}
                for term in query_terms(q):
                    url = f"{base}/empleos-busqueda-{slugify(term)}.html"
                    for page_no in range(1, 4):
                        page = parse_navent_cards(fetch.get_html(url if page_no == 1 else with_param(url, f"page={page_no}"), stealth=True), base)
                        new = [c for c in page if c["url"] not in cards]
                        if not new:
                            break
                        cards.update({c["url"]: c for c in new})
                        if len(cards) >= want:
                            break
                    if len(cards) >= want:
                        break
                for c in list(cards.values())[:want]:
                    jobs.append(self._job(c, cc))
        finally:
            fetch.close_session()
        return jobs[: cap(q)]

    def _job(self, c: dict, cc: str) -> Job:
        job = Job(title=c["title"], company=c["company"], location=c["location"], url=c["url"], source=self.name, posted_at=c["date"], country=cc,
                  work_mode={"remoto": "remote", "híbrido": "hybrid", "presencial": "onsite"}.get(c["mode"].lower()))
        try:
            html = fetch.get_html(c["url"], stealth=True)
        except fetch.NotFound:
            job.closed = True
            return job
        except fetch.FetchError:
            return job
        found = extract_jobs(html, c["url"], self.name)
        if found:
            d = found[0]
            job.description, job.expires_at, job.employment_type = d.description, d.expires_at, d.employment_type
            job.raw["html"] = d.raw.get("html", "")
            job.salary, job.posted_at = d.salary or job.salary, d.posted_at or job.posted_at
            job.company = d.company or job.company
            for f in ("salary_min", "salary_max", "salary_currency", "salary_period"):
                setattr(job, f, getattr(d, f))
        if is_closed_text(clean_html(html)[:3000]):
            job.closed = True
        return job


class OCCSource:
    name, group = "occ", "portal"

    def __init__(self):
        self.countries = set(portal_hosts("occ"))

    @staticmethod
    def parse_ids(html: str) -> list[str]:
        return list(dict.fromkeys(i.removeprefix("jobcard-") for i in Selector(html).css("[id^=jobcard-]::attr(id)").getall()
                                  if i.removeprefix("jobcard-").isdigit()))

    def search(self, q: SearchQuery) -> list[Job]:
        ccs = effective_countries(q.countries, self.countries)
        jobs: list[Job] = []
        try:
            for cc in ccs:
                host = portal_hosts("occ")[cc]
                ids: list[str] = []
                for term in query_terms(q):
                    url = f"https://{host}/empleos/de-{quote(slugify(term))}/"
                    for page_no in range(1, 4):
                        page_ids = self.parse_ids(fetch.get_html(url if page_no == 1 else with_param(url, f"page={page_no}"), stealth=True))
                        new = [i for i in page_ids if i not in ids]
                        if not new:
                            break
                        ids += new
                        if len(ids) >= per_country_cap(q, len(ccs)):
                            break
                    if len(ids) >= per_country_cap(q, len(ccs)):
                        break
                for oid in ids[: per_country_cap(q, len(ccs))]:
                    link = f"https://{host}/empleo/oferta/{oid}/"
                    try:
                        found = extract_jobs(fetch.get_html(link), link, self.name)
                    except fetch.FetchError:
                        continue
                    for j in found:
                        j.country = j.country or cc
                        jobs.append(j)
        finally:
            fetch.close_session()
        return jobs[: cap(q)]


NAVENT_PORTALS = ["bumeran", "zonajobs", "laborum"]
