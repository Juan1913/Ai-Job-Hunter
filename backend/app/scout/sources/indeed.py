"""Indeed por país. El listado trae un JSON incrustado (`mosaic-provider-jobcards`) con título, empresa,
ubicación, salario y fecha: no hace falta abrir cada oferta para eso. La descripción completa se lee de
la página de la oferta (JSON-LD; Indeed la sirve solo a navegador, por eso va con StealthyFetcher) y,
cuando la oferta NO usa 'Indeed Apply', el botón de aplicar se resuelve al sitio real de la empresa
siguiendo la redirección `applystart` (sin descargar el destino).

Verificado en vivo el 2026-10-07: co, mx, es (listado vía Fetcher). Detalle y applystart: mx.

Consulta: UNA sola petición con operadores de Indeed: frases entre comillas y OR entre variantes del rol
(`("analista SOC" OR "SOC analyst")`), que Indeed soporta en `q` (también `-palabra` para excluir).
Antigüedad (`hours_old`): parámetro nativo `fromage` con los valores que Indeed acepta en su propia interfaz (1, 3, 7, 14 días);
otros valores (p. ej. 30) los rechaza con 403, por eso solo se envían estos y, si se pide algo mayor, se filtra en local
(siempre se vuelve a filtrar en local, porque `fromage` es por tramos). `offset` es nativo (`start`)."""
import json
import re
from urllib.parse import quote_plus, urlsplit

from app.domain.geo import effective_countries, place_names, portal_hosts
from app.domain.job import Job, SearchQuery
from app.scout import fetch
from app.scout.base import cap
from app.scout.jsonld import extract_jobs
from app.scout.normalize import clean_html, max_age_days, parse_date
from app.scout.query import boolean_or, query_terms
from app.scout.sources.portals import per_country_cap

MOSAIC = re.compile(r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*')
PERIOD = {"HOURLY": "hour", "DAILY": "day", "WEEKLY": "week", "MONTHLY": "month", "YEARLY": "year"}
FROMAGE_VALID = (1, 3, 7, 14)   # días que acepta Indeed (verificado en vivo: ver scripts/verify_sources.py --probe-indeed-age)
DETAIL_LIMIT = 12       # páginas de detalle (navegador) por búsqueda: caras; el resto usa el fragmento del listado


def parse_cards(html: str) -> list[dict]:
    m = MOSAIC.search(html)
    if not m:
        return []
    try:
        data, _ = json.JSONDecoder().raw_decode(html[m.end():])
        return data["metaData"]["mosaicProviderJobCardsModel"]["results"]
    except (ValueError, KeyError):
        return []


def card_to_job(c: dict, host: str, cc: str, source: str) -> Job | None:
    if c.get("expired") or not c.get("jobkey"):
        return None
    extra = {}
    es = c.get("extractedSalary")
    if isinstance(es, dict) and es.get("min"):
        extra = {"salary_min": float(es["min"]), "salary_max": float(es.get("max") or es["min"]), "salary_period": PERIOD.get(es.get("type")),
                 "salary_currency": (c.get("salarySnippet") or {}).get("currency")}
    snippet = clean_html(c.get("snippet", ""))
    return Job(title=c.get("displayTitle") or c.get("title", ""), company=c.get("company") or c.get("truncatedCompany", ""),
               location=c.get("formattedLocation", ""), remote=bool(c.get("remoteLocation")), work_mode="remote" if c.get("remoteLocation") else None,
               description=snippet, url=f"https://{host}/viewjob?jk={c['jobkey']}", source=source, country=cc,
               salary=(c.get("salarySnippet") or {}).get("text") or None,
               posted_at=parse_date(c.get("pubDate") or c.get("createDate")) or c.get("formattedRelativeTime"),
               raw={"apply_internal": bool(c.get("indeedApplyEnabled")), "apply_start": c.get("thirdPartyApplyUrl")}, **extra)


def fromage_for(hours_old: int | None) -> int | None:
    """Menor valor válido de `fromage` que cubre `hours_old`; None si pasa de 14 días (entonces solo filtro local)."""
    days = max_age_days(hours_old)
    return None if days is None else next((d for d in FROMAGE_VALID if days <= d), None)


class IndeedSource:
    name, group = "indeed", "portal"
    native_offset = True

    def __init__(self):
        self.countries = set(portal_hosts("indeed"))

    def search(self, q: SearchQuery) -> list[Job]:
        ccs = effective_countries(q.countries, self.countries)
        jobs: list[Job] = []
        for cc in ccs:
            host = portal_hosts("indeed")[cc]
            base = f"https://{host}/jobs?q={quote_plus(boolean_or(query_terms(q)))}&l={quote_plus(place_names(q.regions, [cc]))}"
            if (age := fromage_for(q.hours_old)):
                base += f"&fromage={age}"
            want, got = per_country_cap(q, len(ccs)), []
            for page in range(3):
                start = q.offset + 10 * page
                cards = parse_cards(fetch.get_html(base + (f"&start={start}" if start else "")))
                new = [j for c in cards if (j := card_to_job(c, host, cc, self.name)) and j.url not in {x.url for x in got}]
                if not new:
                    break
                got += new
                if len(got) >= want:
                    break
            jobs += got[:want]
        self._enrich(jobs[: cap(q)], detail=q.fetch_description)
        return jobs[: cap(q)]

    def _enrich(self, jobs: list[Job], detail: bool = True) -> None:
        for i, j in enumerate(jobs):
            raw = j.raw or {}
            if not raw.get("apply_internal") and raw.get("apply_start"):
                try:
                    dest = fetch.resolve_redirect(raw["apply_start"].replace("http://", "https://"))
                    if urlsplit(dest).hostname and "indeed." not in urlsplit(dest).hostname:
                        j.apply_url = dest
                except Exception:  # noqa: BLE001 — sin destino externo: se queda la URL de Indeed
                    pass
            if detail and i < DETAIL_LIMIT:
                try:
                    found = extract_jobs(fetch.get_html(j.url, stealth=True), j.url, self.name)
                except Exception:  # noqa: BLE001
                    found = []
                if found:
                    d = found[0]
                    j.description = d.description or j.description
                    j.raw["html"] = d.raw.get("html", "")
                    j.expires_at = d.expires_at or j.expires_at
                    j.employment_type = d.employment_type or j.employment_type
                    for f in ("salary_min", "salary_max", "salary_currency", "salary_period"):
                        if getattr(j, f) is None:
                            setattr(j, f, getattr(d, f))
