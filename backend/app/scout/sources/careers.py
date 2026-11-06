"""Páginas de carreras de cualquier empresa (Bancolombia, MercadoLibre, ...).
1) JSON-LD en la propia página; 2) enlaces que parezcan ofertas, leídos por JSON-LD."""
import re
from urllib.parse import urljoin, urlparse

from scrapling.parser import Selector

from app.domain.job import Job, SearchQuery
from app.scout import fetch
from app.scout.base import filter_company_jobs
from app.scout.jsonld import extract_jobs
from app.scout.normalize import clean_html, is_closed_text

JOB_LINK = re.compile(r"/(job|jobs|vacante|vacantes|empleo|oferta|ofertas|position|positions|careers?)(/[^/?#]+|\?(id|jobId|gh_jid)=)", re.IGNORECASE)

GENERIC_HEADINGS = {"careers", "follow us", "menu", "search"}


class CareerPage:
    countries: set[str] = set()
    group, zero_ok = "careers", True

    def __init__(self, company: str, url: str, stealth: bool = False):
        self.company, self.url, self.stealth, self.name = company, url, stealth, f"careers:{company.lower()}"

    def _from_page(self, html: str, link: str) -> list[Job]:
        """Respaldo sin JSON-LD: título = h1, descripción = texto de la página."""
        page = Selector(html)
        titles = page.css("h1::text").getall() + page.css("h2::text").getall()
        title = " ".join(next((t for t in titles if t.strip() and t.strip().lower() not in GENERIC_HEADINGS), "").split())
        if not title:
            return []
        text = " ".join(" ".join(page.css("body *:not(script):not(style):not(noscript)::text").getall()).split())
        return [Job(title=title, company=self.company, description=text[:6000], url=link, source=self.name)]

    def search(self, q: SearchQuery) -> list[Job]:
        try:
            return self._search(q)
        finally:
            if self.stealth:
                fetch.close_session()

    def _search(self, q: SearchQuery) -> list[Job]:
        html = fetch.get_html(self.url, self.stealth)
        jobs = extract_jobs(html, self.url, self.name)
        if not jobs:
            host = urlparse(self.url).netloc
            links = [urljoin(self.url, h) for h in Selector(html).css("a::attr(href)").getall()]
            links = list(dict.fromkeys(l for l in links if urlparse(l).netloc == host and JOB_LINK.search(urlparse(l).path + ("?" + urlparse(l).query if urlparse(l).query else ""))))
            for link in links[: q.limit_per_source]:
                try:
                    html = fetch.get_html(link, self.stealth)
                    found = extract_jobs(html, link, self.name) or self._from_page(html, link)
                except Exception:  # noqa: BLE001 — una oferta rota no tumba la búsqueda
                    continue
                if is_closed_text(clean_html(html)[:3000]):
                    for j in found:
                        j.closed = True
                jobs += found
        for j in jobs:
            j.company = j.company or self.company
        return filter_company_jobs(jobs, q)
