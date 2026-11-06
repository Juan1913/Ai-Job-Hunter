"""Portales de empleo por HTML. Flujo: página de resultados (varias páginas) -> enlaces a ofertas
-> cada oferta se lee por JSON-LD (JobPosting). Así solo hay que mantener, por portal, la URL de
búsqueda, el selector de enlaces y cómo se pagina.

Enrutamiento por país: cada portal declara en `app/domain/geo.py` los países donde opera y su
subdominio/host; la fuente construye la URL del país (o países) que pidió el usuario.

`verified=True` = comprobado en vivo con Scrapling el 2026-10-07. Los sitios cambian:
si una fuente empieza a devolver 0 ofertas, revisa su selector (scripts/verify_sources.py)."""
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import quote_plus, urljoin, urlsplit, urlunsplit

from scrapling.parser import Selector

from app.domain.geo import effective_countries, place_names, portal_hosts
from app.domain.job import Job, SearchQuery
from app.scout import fetch
from app.scout.base import cap
from app.scout.jsonld import extract_jobs
from app.scout.normalize import clean_html, is_closed_text
from app.scout.query import query_terms


def slugify(text: str) -> str:
    import unicodedata
    s = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def with_param(url: str, param: str) -> str:
    return f"{url}{'&' if '?' in url else '?'}{param}"


def per_country_cap(q: SearchQuery, n_countries: int) -> int:
    return max(1, math.ceil(cap(q) / max(1, n_countries)))


@dataclass
class PortalConfig:
    name: str
    countries: set[str]
    search_url: str          # {host} = host/subdominio del país, {kw} = keywords, {kw_slug} = con guiones, {loc} = ubicación
    link_selector: str       # CSS de los <a> a ofertas
    stealth: bool = False
    verified: bool = False
    strip_query: bool = False                       # URLs con tracking (LinkedIn, Computrabajo)
    detail_fallback: Callable[[str, str, str], list[Job]] | None = None  # si no hay JSON-LD
    portal: str | None = None                       # clave en geo.COUNTRIES[*].portals (None = host fijo en search_url)
    paginate: Callable[[str, int], str] | None = None   # (url_pagina_1, n) -> url de la página n
    max_pages: int = 3
    verified_countries: set[str] = field(default_factory=set)


def computrabajo_detail(html: str, url: str, source: str) -> list[Job]:
    """Computrabajo no publica JobPosting: se lee el HTML de la oferta."""
    page = Selector(html)
    title = (page.css("h1::text").get() or "").strip()
    if not title:
        return []
    company = (page.css("a.dIB.fs16::text").get() or "").strip()
    boxes = list(page.css("div.box_detail"))
    blocks = [re.sub(r"\s+", " ", " ".join(b.css("::text").getall())).strip() for b in boxes]
    best = max(range(len(blocks)), key=lambda i: len(blocks[i]), default=None)
    return [Job(title=title, company=company, description=blocks[best] if best is not None else "", url=url, source=source,
                raw={"html": boxes[best].html_content} if best is not None else {})]


PORTALS = [
    PortalConfig("computrabajo", set(portal_hosts("computrabajo")), "https://{host}.computrabajo.com/trabajo-de-{kw_slug}",
                 "a[href*='/ofertas-de-trabajo/oferta']", verified=True, strip_query=True, detail_fallback=computrabajo_detail,
                 portal="computrabajo", paginate=lambda u, n: with_param(u, f"p={n}"),
                 verified_countries={"CO", "MX", "AR", "CL", "PE", "EC", "UY"}),
    PortalConfig("trabajosdiarios", set(portal_hosts("trabajosdiarios")), "https://{host}/ofertas-trabajo/de-{kw_slug}",
                 "a[href^='/trabajo/']", verified=True, portal="trabajosdiarios", paginate=lambda u, n: with_param(u, f"page={n}"),
                 verified_countries={"CO"}),
    PortalConfig("magneto", set(portal_hosts("magneto")), "https://www.magneto365.com/{host}/trabajos/buscar?q={kw}",
                 "a[href*='/co/empleos/']", verified=True, portal="magneto",
                 paginate=lambda u, n: with_param(u, f"paginator%5Bpage%5D={n}"), verified_countries={"CO"}),
    PortalConfig("elempleo", set(portal_hosts("elempleo")), "https://www.elempleo.com/{host}/ofertas-empleo/?Search={kw}",
                 "a[href*='/co/ofertas-trabajo/']", verified=True, portal="elempleo",
                 paginate=lambda u, n: with_param(u, f"PageNum={n}"), verified_countries={"CO"}),
]


def _norm(url: str, strip: bool) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, "" if strip else p.query, ""))


class PortalSource:
    group = "portal"

    def __init__(self, cfg: PortalConfig):
        self.cfg, self.name, self.countries = cfg, cfg.name, set(cfg.countries)

    def _countries(self, q: SearchQuery) -> list[str | None]:
        if not self.cfg.portal:
            return [None]
        return effective_countries(q.countries, self.cfg.countries)

    def search_urls(self, q: SearchQuery, term: str | None = None) -> list[tuple[str | None, str]]:
        """(país, URL de la página 1) por cada país pedido que el portal cubre. `term` = una consulta concreta (variante)."""
        kw = " ".join(q.keywords) if term is None else term
        out = []
        for cc in self._countries(q):
            host = portal_hosts(self.cfg.portal).get(cc, "") if cc else ""
            out.append((cc, self.cfg.search_url.format(host=host, kw=quote_plus(kw), kw_slug=slugify(kw),
                                                       loc=quote_plus(place_names(q.regions, [cc] if cc else q.countries)))))
        return out

    def _links(self, url: str, limit: int) -> list[str]:
        links: dict[str, None] = {}
        for page_no in range(1, self.cfg.max_pages + 1):
            page_url = url if page_no == 1 or not self.cfg.paginate else self.cfg.paginate(url, page_no)
            if page_no > 1 and not self.cfg.paginate:
                break
            page = Selector(fetch.get_html(page_url, self.cfg.stealth), url=page_url)
            hrefs = page.css(f"{self.cfg.link_selector}::attr(href)").getall()
            before = len(links)
            links.update(dict.fromkeys(_norm(urljoin(page_url, h), self.cfg.strip_query) for h in hrefs))
            if len(links) >= limit or len(links) == before:       # alcanzó el límite o la página ya no aporta ofertas
                break
        return list(links)[:limit]

    def _detail(self, link: str) -> list[Job]:
        try:
            html = fetch.get_html(link, self.cfg.stealth)
        except Exception:  # noqa: BLE001 — 404/bloqueo/parseo en una oferta: no tumba la búsqueda
            return []
        found = extract_jobs(html, link, self.name)
        if not found and self.cfg.detail_fallback:
            found = self.cfg.detail_fallback(html, link, self.name)
        if is_closed_text(clean_html(html)[:6000]):
            for j in found:
                j.closed = True
        return found

    def search(self, q: SearchQuery) -> list[Job]:
        """Una consulta por término (original + variantes, máx. `request_budget`); se detiene al llenar el cupo del país."""
        terms = query_terms(q)
        targets = self.search_urls(q, terms[0])
        jobs: list[Job] = []
        for cc, _ in targets:
            limit, links = per_country_cap(q, len(targets)), {}
            for term in terms:
                url = dict(self.search_urls(q, term)).get(cc)
                if url is None:
                    continue
                for link in self._links(url, limit - len(links)):
                    links.setdefault(link)
                if len(links) >= limit:
                    break
            for link in links:
                for j in self._detail(link):
                    j.country = j.country or cc
                    jobs.append(j)
        return jobs
