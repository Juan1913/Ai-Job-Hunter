"""LinkedIn vía API guest pública (la misma que usa la web sin sesión). Sin login ni credenciales.

Listado: /jobs-guest/jobs/api/seeMoreJobPostings/search (tarjetas HTML, paginado por `start`).
Detalle: /jobs-guest/jobs/api/jobPosting/{id} (descripción, criterios, estado 'cerrada').

Consulta: una sola petición con operadores booleanos de LinkedIn (`("analista SOC" OR "SOC analyst")`).
Filtros NATIVOS: `f_TPR=r<segundos>` (antigüedad exacta en segundos: `hours_old` x 3600; sin `hours_old` se mantiene el máximo de 30 días),
`f_JT` (F full_time, P part_time, C contract, T temporary, I internship), `f_WT` (1 onsite, 2 remote, 3 hybrid) y `start` (offset).
Ritmo: 0,4 peticiones/s por dominio, 429 -> circuit breaker con cooldown creciente (fetch.py), proxies opcionales (JH_PROXIES).

Límite conocido: para ofertas con 'Apply' externo LinkedIn solo muestra la URL real a usuarios con sesión; en modo
guest se conserva la URL pública de la oferta y, si la página expone `applyUrl`, se resuelve el destino.
Verificado en vivo el 2026-10-07 (Colombia)."""
import re
from urllib.parse import parse_qs, quote_plus, unquote, urlsplit

from scrapling.parser import Selector

from app.domain.geo import COUNTRIES, effective_countries, place_names
from app.domain.job import Job, SearchQuery
from app.scout import fetch
from app.scout.base import cap
from app.scout.normalize import clean_html, is_closed_text, normalize_employment
from app.scout.query import boolean_or, query_terms
from app.scout.sources.portals import per_country_cap

SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"


def parse_cards(html: str) -> list[dict]:
    out = []
    for li in Selector(html).css("li"):
        urn = li.css("div[data-entity-urn]::attr(data-entity-urn)").get() or ""
        m = re.search(r"jobPosting:(\d+)", urn)
        if not m:
            continue
        out.append({"id": m.group(1), "title": " ".join((li.css("h3.base-search-card__title::text").get() or "").split()),
                    "company": " ".join(li.css("h4.base-search-card__subtitle *::text").getall()).strip() or " ".join(li.css("h4::text").getall()).strip(),
                    "location": " ".join((li.css("span.job-search-card__location::text").get() or "").split()),
                    "date": li.css("time::attr(datetime)").get()})
    return out


def parse_detail(html: str) -> dict:
    page = Selector(html)
    markup = page.css("div.show-more-less-html__markup")
    criteria = {" ".join(li.css("h3::text").get("").split()).lower(): " ".join(li.css("span::text").get("").split())
                for li in page.css("li.description__job-criteria-item")}
    apply_raw = re.search(r'id="applyUrl"[^>]*>\s*<!--"?([^"<]+)"?-->', html)
    closed = bool(page.css("figure.closed-job, .closed-job")) or is_closed_text(clean_html(html)[:4000])
    return {"description": clean_html(markup[0].html_content) if markup else "", "html": markup[0].html_content if markup else "", "criteria": criteria, "closed": closed,
            "apply": _real_apply(apply_raw.group(1)) if apply_raw else None}


def _real_apply(url: str) -> str | None:
    """`/jobs/view/externalApply/{id}?url=<destino>` -> destino."""
    qs = parse_qs(urlsplit(url).query)
    dest = unquote(qs["url"][0]) if qs.get("url") else None
    return dest if dest and not urlsplit(dest).hostname.endswith("linkedin.com") else None


JT = {"full_time": "F", "part_time": "P", "contract": "C", "temporary": "T", "internship": "I"}
WT = {"onsite": "1", "remote": "2", "hybrid": "3"}


def search_params(q: SearchQuery) -> str:
    """Filtros nativos de LinkedIn derivados de la consulta (se vuelven a aplicar en local: LinkedIn puede ignorarlos)."""
    out = f"&f_TPR=r{q.hours_old * 3600 if q.hours_old else 2592000}"
    if jt := ",".join(JT[t] for t in dict.fromkeys(q.employment_types) if t in JT):
        out += f"&f_JT={jt}"
    wt = ",".join(WT[m] for m in dict.fromkeys(q.work_modes) if m in WT)
    if wt:
        out += f"&f_WT={wt}"
    elif q.remote and not q.countries and not q.regions:
        out += "&f_WT=2"
    return out


class LinkedInSource:
    name, group = "linkedin", "portal"
    native_offset = True

    def __init__(self):
        self.countries = set(COUNTRIES)

    def search(self, q: SearchQuery) -> list[Job]:
        ccs = effective_countries(q.countries, self.countries) or [None]
        jobs: list[Job] = []
        for cc in ccs:
            want = per_country_cap(q, len(ccs))
            loc = place_names(q.regions, [cc] if cc else [])
            cards: dict[str, dict] = {}
            for _ in range(4):
                url = f"{SEARCH}?keywords={quote_plus(boolean_or(query_terms(q)))}&location={quote_plus(loc)}&start={q.offset + len(cards)}{search_params(q)}"
                page = parse_cards(fetch.get_html(url))
                new = [c for c in page if c["id"] not in cards]
                if not new:
                    break
                cards.update({c["id"]: c for c in new})
                if len(cards) >= want:
                    break
            for c in list(cards.values())[:want]:
                jobs.append(self._job(c, cc, q.fetch_description))
        return jobs[: cap(q)]

    def _job(self, c: dict, cc: str | None, detail: bool = True) -> Job:
        url = f"https://www.linkedin.com/jobs/view/{c['id']}"
        job = Job(title=c["title"], company=c["company"], location=c["location"], url=url, source=self.name, posted_at=c["date"], country=cc)
        if not detail:
            return job
        try:
            d = parse_detail(fetch.get_html(DETAIL.format(id=c["id"])))
        except fetch.NotFound:
            job.closed = True
            return job
        except fetch.FetchError:
            return job
        job.description = d["description"]
        job.raw["html"] = d["html"]
        job.closed = d["closed"]
        job.apply_url = d["apply"]
        job.employment_type = normalize_employment(d["criteria"].get("employment type") or d["criteria"].get("tipo de empleo"))
        return job
