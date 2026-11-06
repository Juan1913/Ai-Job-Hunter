"""Fuentes con API/JSON/RSS pública: estables, sin scraping frágil. Todas leen vía Scrapling (`fetch`).

Verificadas en vivo el 2026-10-07: Remotive, Arbeitnow, RemoteOK, WeWorkRemotely (RSS), Jobicy,
Working Nomads, HN 'Who is hiring' (Algolia), Get on Board. Algunas devuelven TODO el catálogo
(no aceptan búsqueda): se filtra por palabras clave localmente."""
import re
from html import unescape

from lxml import etree

from app.domain.job import Job, SearchQuery
from app.scout import fetch
from app.scout.base import all_terms, cap, filter_company_jobs, geo_ok, match_keywords
from app.scout.normalize import clean_html
from app.scout.query import query_terms

_CLEAN = clean_html


def parse_xml(text: str):
    """XML de terceros: sin entidades externas ni red."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True, huge_tree=False)
    return etree.fromstring(text.encode("utf-8") if isinstance(text, str) else text, parser=parser)


def _txt(el, path: str) -> str:
    n = el.find(path)
    return (n.text or "").strip() if n is not None and n.text else ""


def _kw(q: SearchQuery) -> str:
    return " ".join(q.keywords)


def _lang_ok(job_lang: str | None, q: SearchQuery) -> bool:
    return not q.languages or not job_lang or job_lang in q.languages


# ---------------------------------------------------------------- feeds remotos
class Remotive:
    name, countries, remote_only, group = "remotive", set(), True, "api"

    def search(self, q: SearchQuery) -> list[Job]:
        jobs, seen = [], set()
        for term in query_terms(q):
            data = fetch.get_json("https://remotive.com/api/remote-jobs", params={"search": term, "limit": cap(q) * 2})
            rows = data.get("jobs", [])
            for j in rows:
                loc = j.get("candidate_required_location", "")
                if j["url"] in seen or not geo_ok(loc, q):
                    continue
                seen.add(j["url"])
                jobs.append(self._job(j, loc))
            if len(jobs) >= cap(q):
                break
        return jobs[: cap(q)]

    def _job(self, j: dict, loc: str) -> Job:
        return (Job(title=j["title"], company=j["company_name"], location=loc, remote=True, work_mode="remote",
                            description=_CLEAN(j.get("description", "")), raw={"html": j.get("description", "")}, url=j["url"], source=self.name,
                            posted_at=j.get("publication_date"), salary=j.get("salary") or None,
                            employment_type=j.get("job_type") or None))


class Arbeitnow:
    """https://www.arbeitnow.com/api/job-board-api — principalmente Europa (DE), con filtro `remote` en el JSON."""
    name, countries, remote_only, group = "arbeitnow", set(), False, "api"

    def search(self, q: SearchQuery) -> list[Job]:
        out: list[Job] = []
        for page in range(1, 4):
            data = fetch.get_json("https://www.arbeitnow.com/api/job-board-api", params={"page": page})
            rows = data.get("data", [])
            for j in rows:
                blob = f"{j.get('title', '')} {' '.join(j.get('tags') or [])} {j.get('description', '')[:1500]}"
                if not match_keywords(blob, all_terms(q)):
                    continue
                remote = bool(j.get("remote"))
                if q.countries and not remote and not geo_ok(j.get("location", ""), q):
                    continue
                out.append(Job(title=j["title"], company=j.get("company_name", ""), location=j.get("location", ""), remote=remote,
                               description=_CLEAN(j.get("description", "")), raw={"html": j.get("description", "")}, url=j["url"], source=self.name,
                               posted_at=j.get("created_at"), employment_type=(j.get("job_types") or [None])[0]))
            if len(out) >= cap(q) or not rows or not (data.get("links") or {}).get("next"):
                break
        return out[: cap(q)]


class RemoteOK:
    """https://remoteok.com/api — el primer elemento es un aviso legal. Condición de uso: enlazar a RemoteOK (la URL es la suya)."""
    name, countries, remote_only, group = "remoteok", set(), True, "api"

    def search(self, q: SearchQuery) -> list[Job]:
        data = fetch.get_json("https://remoteok.com/api")
        out = []
        for j in (x for x in data if isinstance(x, dict) and x.get("position")):
            blob = f"{j['position']} {' '.join(j.get('tags') or [])} {j.get('description', '')[:1500]}"
            if not match_keywords(blob, all_terms(q)) or not geo_ok(j.get("location", ""), q):
                continue
            lo, hi = int(j.get("salary_min") or 0), int(j.get("salary_max") or 0)
            out.append(Job(title=j["position"], company=j.get("company", ""), location=j.get("location", ""), remote=True, work_mode="remote",
                           description=_CLEAN(j.get("description", "")), raw={"html": j.get("description", "")}, url=j.get("url") or j.get("apply_url"), source=self.name,
                           posted_at=j.get("date"), apply_url=j.get("apply_url"),
                           **({"salary_min": lo, "salary_max": hi or lo, "salary_currency": "USD", "salary_period": "year",
                               "salary": f"{lo}-{hi} USD"} if lo else {})))
        return out[: cap(q)]


class WeWorkRemotely:
    """RSS público: https://weworkremotely.com/remote-jobs.rss (campos region/country/expires_at)."""
    name, countries, remote_only, group = "weworkremotely", set(), True, "api"

    def search(self, q: SearchQuery) -> list[Job]:
        root = parse_xml(fetch.get_xml("https://weworkremotely.com/remote-jobs.rss"))
        out = []
        for it in root.iter("item"):
            raw_title = _txt(it, "title")
            company, _, title = raw_title.partition(": ")
            title = title or raw_title
            desc = _CLEAN(_txt(it, "description"))
            region = ", ".join(x for x in (_txt(it, "region"), _txt(it, "country")) if x)
            if not match_keywords(f"{title} {_txt(it, 'category')} {desc[:1500]}", all_terms(q)) or not geo_ok(region, q):
                continue
            out.append(Job(title=title, company=company if title != raw_title else "", location=region, remote=True, work_mode="remote",
                           description=desc, raw={"html": _txt(it, "description")}, url=_txt(it, "link") or _txt(it, "guid"), source=self.name, posted_at=_txt(it, "pubDate"),
                           expires_at=_txt(it, "expires_at") or None, employment_type=_txt(it, "type") or None))
        return out[: cap(q)]


class Jobicy:
    """https://jobicy.com/api/v2/remote-jobs — condición de uso: atribución y el botón de aplicar lleva a la URL original."""
    name, countries, remote_only, group = "jobicy", set(), True, "api"

    def search(self, q: SearchQuery) -> list[Job]:
        out, seen = [], set()
        rows: list[dict] = []
        for term in query_terms(q):
            rows += fetch.get_json("https://jobicy.com/api/v2/remote-jobs", params={"count": min(cap(q) * 2, 50), "tag": term}).get("jobs", [])
            if len(rows) >= cap(q) * 2:
                break
        for j in rows:
            geo = j.get("jobGeo", "")
            if j["url"] in seen or not geo_ok(geo, q):
                continue
            seen.add(j["url"])
            extra = {}
            if j.get("salaryMin"):
                extra = {"salary_min": j["salaryMin"], "salary_max": j.get("salaryMax") or j["salaryMin"],
                         "salary_currency": j.get("salaryCurrency"), "salary_period": {"yearly": "year", "monthly": "month", "hourly": "hour"}.get(j.get("salaryPeriod"))}
            out.append(Job(title=unescape(j["jobTitle"]), company=unescape(j.get("companyName", "")), location=geo, remote=True, work_mode="remote",
                           description=_CLEAN(j.get("jobDescription") or j.get("jobExcerpt", "")), raw={"html": j.get("jobDescription") or j.get("jobExcerpt", "")},
                           url=j["url"], source=self.name,
                           posted_at=j.get("pubDate"), employment_type=(j.get("jobType") or [None])[0], **extra))
        return out[: cap(q)]


class WorkingNomads:
    """https://www.workingnomads.com/api/exposed_jobs/ — catálogo completo (~50), filtrado localmente."""
    name, countries, remote_only, group = "workingnomads", set(), True, "api"

    def search(self, q: SearchQuery) -> list[Job]:
        out = []
        for j in fetch.get_json("https://www.workingnomads.com/api/exposed_jobs/"):
            blob = f"{j.get('title', '')} {j.get('tags', '')} {j.get('category_name', '')} {j.get('description', '')[:1500]}"
            if not match_keywords(blob, all_terms(q)) or not geo_ok(j.get("location", ""), q):
                continue
            out.append(Job(title=j["title"], company=j.get("company_name", ""), location=j.get("location", ""), remote=True, work_mode="remote",
                           description=_CLEAN(j.get("description", "")), raw={"html": j.get("description", "")}, url=j["url"], source=self.name, posted_at=j.get("pub_date")))
        return out[: cap(q)]


class HNHiring:
    """Hilo mensual 'Ask HN: Who is hiring?' vía Algolia (comentarios de primer nivel)."""
    name, countries, remote_only, group = "hn_hiring", set(), False, "api"
    API = "https://hn.algolia.com/api/v1"

    def _story(self) -> str | None:
        data = fetch.get_json(f"{self.API}/search_by_date", params={"tags": "author_whoishiring,story", "hitsPerPage": 6})
        for h in data.get("hits", []):
            if re.search(r"who is hiring", h.get("title", ""), re.I):
                return h["objectID"]
        return None

    @staticmethod
    def parse_comment(c: dict, source: str) -> Job | None:
        html = c.get("comment_text") or ""
        lines = [clean_html(x) for x in re.split(r"<p>|<br\s*/?>", html) if clean_html(x)]
        if not lines or "|" not in lines[0]:
            return None                     # formato libre: sin título fiable, se descarta
        parts = [p.strip() for p in lines[0].split("|") if p.strip()]
        company = re.sub(r"\s*\(.*?\)\s*$", "", parts[0])
        rest = parts[1:]
        loc_re = re.compile(r"remote|onsite|on-site|hybrid|, ?[A-Z]{2}\b|usa|uk|europe|germany|berlin|london|new york|san francisco|canada|latam|worldwide", re.I)
        title = next((p for p in rest if not loc_re.search(p) and not p.startswith("http") and not re.search(r"\$|€|£|\d+k", p)), rest[0] if rest else "")
        loc = ", ".join(p for p in rest if loc_re.search(p))
        links = re.findall(r'href="(https?://[^"]+)"', html)
        text = clean_html(html)
        return Job(title=title[:200] or "(sin título)", company=company, location=loc, remote=bool(re.search(r"remote", lines[0], re.I)),
                   description=text, raw={"html": html}, url=f"https://news.ycombinator.com/item?id={c['objectID']}", source=source,
                   posted_at=c.get("created_at"), apply_url=links[0] if links else None)

    def search(self, q: SearchQuery) -> list[Job]:
        story = self._story()
        if not story:
            return []
        out: list[Job] = []
        seen: set[str] = set()
        for term in query_terms(q, min(q.request_budget, 3)):          # cada consulta pagina hasta 3 veces: tope corto
            for page in range(0, 3):
                data = fetch.get_json(f"{self.API}/search", params={"tags": f"comment,story_{story}", "query": term, "hitsPerPage": 50, "page": page})
                for c in data.get("hits", []):
                    if str(c.get("parent_id")) != str(story) or c["objectID"] in seen:
                        continue
                    seen.add(c["objectID"])
                    job = self.parse_comment(c, self.name)
                    if not job or (q.countries and job.location and not job.remote and not geo_ok(job.location, q)):
                        continue
                    out.append(job)
                if len(out) >= cap(q) or page + 1 >= data.get("nbPages", 0):
                    break
            if len(out) >= cap(q):
                break
        return out[: cap(q)]


class GetOnBoard:
    """Get on Board (LATAM tech): https://www.getonbrd.com/api/v0/search/jobs. Filtra por idioma y país del usuario."""
    name, countries, remote_only, group = "getonboard", set(), False, "api"
    LANG = {"lang_not_specified": None, "es": "es", "en": "en", "pt": "pt"}

    def search(self, q: SearchQuery) -> list[Job]:
        out: list[Job] = []
        seen: set[str] = set()
        for term in query_terms(q, min(q.request_budget, 3)):
            out += self._query(q, term, seen)
            if len(out) >= cap(q):
                break
        return out[: cap(q)]

    def _query(self, q: SearchQuery, term: str, seen: set[str]) -> list[Job]:
        out: list[Job] = []
        for page in range(1, 4):
            data = fetch.get_json("https://www.getonbrd.com/api/v0/search/jobs",
                                  params={"query": term, "per_page": min(cap(q), 50), "page": page, "expand[]": "company"})
            for row in data.get("data", []):
                if row["links"]["public_url"] in seen:
                    continue
                seen.add(row["links"]["public_url"])
                a = row["attributes"]
                countries = a.get("countries") or []
                lang = self.LANG.get(a.get("lang"))
                remote_mod = a.get("remote_modality") or ""
                remote = remote_mod.startswith("remote")
                places = ", ".join(c for c in countries if c != "Remote")
                if not _lang_ok(lang, q) or (remote_mod != "remote_global" and not geo_ok(places, q)):
                    continue
                company = (((a.get("company") or {}).get("data") or {}).get("attributes") or {}).get("name", "")
                html = " ".join(str(a.get(k) or "") for k in ("description", "functions", "benefits", "desirable"))
                desc = _CLEAN(html)
                extra = {"salary_min": a["min_salary"], "salary_max": a.get("max_salary") or a["min_salary"], "salary_currency": "USD",
                         "salary_period": "month"} if a.get("min_salary") else {}
                out.append(Job(title=a["title"], company=company, location=", ".join(countries), remote=remote,
                               work_mode={"hybrid": "hybrid", "no_remote": "onsite"}.get(remote_mod) or ("remote" if remote else None),
                               description=desc, raw={"html": html}, url=row["links"]["public_url"], source=self.name, posted_at=a.get("published_at"),
                               language=lang, **extra))
            meta = data.get("meta", {})
            if len(out) >= cap(q) or page >= meta.get("total_pages", 1):
                break
        return out


# ---------------------------------------------------------------- ATS clásicos (token por empresa)
class Greenhouse:
    """Tableros de empresas en Greenhouse: boards-api.greenhouse.io/v1/boards/{token}/jobs"""
    countries: set[str] = set()
    group, zero_ok = "ats", True   # zero_ok: un tablero de empresa sin vacantes relevantes es normal

    def __init__(self, token: str, company: str):
        self.token, self.company, self.name = token, company, f"greenhouse:{token}"

    def search(self, q: SearchQuery) -> list[Job]:
        data = fetch.get_json(f"https://boards-api.greenhouse.io/v1/boards/{self.token}/jobs", params={"content": "true"})
        return filter_company_jobs([
            Job(title=j["title"], company=self.company, location=(j.get("location") or {}).get("name", ""),
                description=clean_html(j.get("content", "")), raw={"html": j.get("content", "")}, url=j["absolute_url"], source=self.name,
                posted_at=j.get("first_published") or j.get("updated_at"), apply_url=j["absolute_url"],
                language=j.get("language") if j.get("language") in ("es", "en", "pt") else None)
            for j in data.get("jobs", [])], q)


class Lever:
    """Tableros en Lever: api.lever.co/v0/postings/{company}?mode=json"""
    countries: set[str] = set()
    group, zero_ok = "ats", True   # zero_ok: un tablero de empresa sin vacantes relevantes es normal

    def __init__(self, token: str, company: str):
        self.token, self.company, self.name = token, company, f"lever:{token}"

    @staticmethod
    def _html(j: dict) -> str:
        """descripción + listas (requisitos, beneficios...) + cierre: así el markdown conserva los encabezados y viñetas."""
        lists = "".join(f"<h3>{x.get('text', '')}</h3><ul>{x.get('content', '')}</ul>" for x in j.get("lists") or [])
        return f"{j.get('description', '')}{lists}{j.get('additional', '')}"

    def search(self, q: SearchQuery) -> list[Job]:
        data = fetch.get_json(f"https://api.lever.co/v0/postings/{self.token}", params={"mode": "json"})
        out = []
        for j in data:
            cat = j.get("categories") or {}
            out.append(Job(title=j["text"], company=self.company, location=cat.get("location", ""),
                            description=clean_html(j.get("descriptionPlain") or j.get("description", "")), raw={"html": self._html(j)}, url=j["hostedUrl"],
                            source=self.name, apply_url=j.get("applyUrl"), posted_at=j.get("createdAt"),
                            country=j.get("country"), work_mode=j.get("workplaceType") if j.get("workplaceType") in ("remote", "hybrid") else
                            ("onsite" if j.get("workplaceType") == "on-site" else None), employment_type=cat.get("commitment")))
        return filter_company_jobs(out, q)


# ---------------------------------------------------------------- APIs con clave gratuita (opcionales: sin clave NO se registran)
def _days(q: SearchQuery) -> int | None:
    from app.scout.normalize import max_age_days
    return max_age_days(q.hours_old)


class Adzuna:
    """https://developer.adzuna.com — API oficial con `app_id`/`app_key` gratuitos (JH_ADZUNA_APP_ID / JH_ADZUNA_APP_KEY).
    Agregador: la descripción es un FRAGMENTO y `redirect_url` pasa por Adzuna (no hay URL del empleador). Filtros NATIVOS:
    `max_days_old` (antigüedad), `full_time`/`part_time`/`contract`/`permanent`, `what` (consulta) y `where`. Los salarios con
    `salary_is_predicted=1` son estimaciones de Adzuna y se IGNORAN. Países con portal Adzuna que también están en nuestra geografía."""
    name, group, remote_only = "adzuna", "api", False
    COUNTRY_CODES = {"MX", "ES", "BR", "US", "CA", "GB", "DE", "FR", "IT"}      # ISO -> adzuna usa el mismo código en minúscula
    countries = set(COUNTRY_CODES)
    API = "https://api.adzuna.com/v1/api/jobs"

    def __init__(self, app_id: str, app_key: str):
        self.app_id, self.app_key = app_id, app_key

    def _params(self, q: SearchQuery, term: str, cc: str) -> dict:
        from app.domain.geo import place_names
        p: dict = {"app_id": self.app_id, "app_key": self.app_key, "what": term, "results_per_page": min(cap(q), 50), "content-type": "application/json"}
        if q.regions:
            p["where"] = place_names(q.regions, [cc])
        if (d := _days(q)):
            p["max_days_old"] = d
        types = set(q.employment_types)
        if types == {"full_time"}:
            p["full_time"] = 1
        elif types == {"part_time"}:
            p["part_time"] = 1
        elif types == {"contract"}:
            p["contract"] = 1
        return p

    @staticmethod
    def parse(r: dict, cc: str, source: str) -> Job:
        from app.domain.geo import default_currency
        pred = str(r.get("salary_is_predicted", "0")) == "1"
        extra = {}
        if r.get("salary_min") and not pred:
            extra = {"salary_min": float(r["salary_min"]), "salary_max": float(r.get("salary_max") or r["salary_min"]), "salary_period": "year",
                     "salary_currency": default_currency(cc)}
        emp = r.get("contract_time") or r.get("contract_type")
        return Job(title=_CLEAN(r.get("title", "")), company=(r.get("company") or {}).get("display_name", ""),
                   location=(r.get("location") or {}).get("display_name", ""), description=_CLEAN(r.get("description", "")), url=r["redirect_url"],
                   source=source, posted_at=r.get("created"), employment_type=emp, country=cc, **extra)

    def search(self, q: SearchQuery) -> list[Job]:
        from app.domain.geo import effective_countries
        out: list[Job] = []
        seen: set[str] = set()
        ccs = effective_countries(q.countries, self.countries)
        for cc in ccs:
            for term in query_terms(q, min(q.request_budget, 3)):
                data = fetch.get_json(f"{self.API}/{cc.lower()}/search/{1 + q.offset // max(1, min(cap(q), 50))}", params=self._params(q, term, cc))
                for r in data.get("results", []):
                    if r["redirect_url"] not in seen:
                        seen.add(r["redirect_url"])
                        out.append(self.parse(r, cc, self.name))
                if len(out) >= cap(q):
                    break
        return out[: cap(q)]


class Jooble:
    """https://jooble.org/api/about — REST con clave gratuita (JH_JOOBLE_API_KEY). La clave gratuita da 500 peticiones DE POR VIDA y
    es por país: se usa con presupuesto mínimo (UNA petición por búsqueda, una sola página, sin variantes). `snippet` es un fragmento;
    el enlace pasa por Jooble. `datecreatedfrom` (antigüedad) se envía y además se filtra en local."""
    name, group, remote_only, countries = "jooble", "api", False, set()
    API = "https://jooble.org/api"

    def __init__(self, api_key: str):
        self.api_key = api_key

    @staticmethod
    def parse(r: dict, source: str) -> Job:
        salary = re.sub(r"(?<=\d)[ \u00a0](?=\d{3}\b)", "", r.get("salary") or "") or None         # '20 000 - 25 000 MXN'
        return Job(title=_CLEAN(r.get("title", "")), company=r.get("company", ""), location=r.get("location", ""),
                   description=_CLEAN(r.get("snippet", "")), url=r["link"], source=source, posted_at=r.get("updated"), salary=salary,
                   employment_type=r.get("type") or None)

    def search(self, q: SearchQuery) -> list[Job]:
        from datetime import datetime, timedelta, timezone

        from app.domain.geo import place_names
        body = {"keywords": query_terms(q, 1)[0], "location": place_names(q.regions, q.countries[:1]), "page": 1 + q.offset // 20, "ResultOnPage": min(cap(q), 20)}
        if (d := _days(q)):
            body["datecreatedfrom"] = (datetime.now(timezone.utc) - timedelta(days=d)).strftime("%Y-%m-%d")
        data = fetch.get_json(f"{self.API}/{self.api_key}", method="POST", json_body=body)
        return [self.parse(r, self.name) for r in data.get("jobs", []) if r.get("link")][: cap(q)]


class InfoJobs:
    """API OFICIAL de InfoJobs (España) con credenciales de aplicación (JH_INFOJOBS_CLIENT_ID / _SECRET; Basic auth).
    El scraping del sitio web NO se hace (ToS + robots.txt + antibot). Filtro nativo de antigüedad `sinceDate`
    (_24_HOURS | _7_DAYS | _15_DAYS | ANY); el detalle (descripción completa) cuesta 1 petición por oferta y respeta `fetch_description`."""
    name, group, remote_only, countries = "infojobs", "api", False, {"ES"}
    API = "https://api.infojobs.net/api"
    DETAIL_LIMIT = 10

    def __init__(self, client_id: str, client_secret: str):
        import base64
        self._auth = {"Authorization": "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()}

    @staticmethod
    def since(days: int | None) -> str:
        return "ANY" if days is None else "_24_HOURS" if days <= 1 else "_7_DAYS" if days <= 7 else "_15_DAYS" if days <= 15 else "ANY"

    @staticmethod
    def parse(o: dict, source: str) -> Job:
        sal = o.get("salaryDescription") or None
        return Job(title=_CLEAN(o.get("title", "")), company=(o.get("author") or {}).get("name", ""),
                   location=", ".join(x for x in (o.get("city"), (o.get("province") or {}).get("value")) if x), url=o["link"], source=source,
                   posted_at=o.get("published") or o.get("updated"), salary=sal, country="ES",
                   work_mode="remote" if "solo teletrabajo" in str((o.get("teleworking") or {}).get("value", "")).lower() else None,
                   employment_type=(o.get("contractType") or {}).get("value"))

    def search(self, q: SearchQuery) -> list[Job]:
        out: list[Job] = []
        seen: set[str] = set()
        for term in query_terms(q, min(q.request_budget, 3)):
            data = fetch.get_json(f"{self.API}/9/offer", params={"q": term, "maxResults": min(cap(q), 50), "page": 1 + q.offset // max(1, min(cap(q), 50)),
                                                                 "sinceDate": self.since(_days(q))}, headers=self._auth)
            for o in data.get("offers", []):
                if o["link"] not in seen:
                    seen.add(o["link"])
                    out.append(self.parse(o, self.name))
            if len(out) >= cap(q):
                break
        out = out[: cap(q)]
        if q.fetch_description:
            for j in out[: self.DETAIL_LIMIT]:
                try:
                    oid = j.url.rstrip("/").split("/")[-1].split("?")[0]
                    d = fetch.get_json(f"{self.API}/7/offer/{oid}", headers=self._auth)
                except fetch.FetchError:
                    continue
                j.description = _CLEAN(d.get("description", "")) or j.description
                j.raw["html"] = d.get("description", "")
        return out
