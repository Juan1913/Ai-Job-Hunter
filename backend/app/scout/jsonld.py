"""Extrae ofertas schema.org/JobPosting incrustadas en HTML (JSON-LD).
La mayoría de portales y páginas de carreras las publican para Google Jobs."""
import json

from scrapling.parser import Selector

from app.domain.job import Job
from app.scout.normalize import clean_html, normalize_employment, parse_date

_clean = clean_html     # alias histórico (api_sources lo importaba)


def _lower_keys(node):
    """OCC publica 'Title'/'Description'...: se normalizan las claves a minúsculas iniciales."""
    if isinstance(node, dict):
        return {(k[:1].lower() + k[1:] if k[:1].isupper() and not k.startswith("@") else k): _lower_keys(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_lower_keys(x) for x in node]
    return node


def _is_posting(node: dict) -> bool:
    t = node.get("@type")
    return t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t)


def _iter_postings(node):
    if isinstance(node, list):
        for n in node:
            yield from _iter_postings(n)
    elif isinstance(node, dict):
        if _is_posting(node):
            yield node
        for key in ("@graph", "itemListElement", "item"):
            if key in node:
                yield from _iter_postings(node[key])


def _flat(v) -> list[str]:
    if isinstance(v, str):
        return [v] if v else []
    if isinstance(v, dict):
        return _flat(v.get("name"))
    if isinstance(v, list):
        return [x for i in v for x in _flat(i)]
    return []


def _location(p: dict) -> str:
    loc = p.get("jobLocation")
    loc = loc[0] if isinstance(loc, list) and loc else loc
    addr = loc.get("address", {}) if isinstance(loc, dict) else {}
    if isinstance(addr, str):
        return addr
    return ", ".join(_flat(addr.get("addressLocality")) + _flat(addr.get("addressRegion")) + _flat(addr.get("addressCountry")))


_UNIT = {"HOUR": "hour", "DAY": "day", "WEEK": "week", "MONTH": "month", "YEAR": "year"}


def _salary(p: dict) -> tuple[str | None, dict]:
    """(texto, campos numéricos) del baseSalary."""
    base = p.get("baseSalary")
    if not isinstance(base, dict):
        return None, {}
    v = base.get("value")
    cur = base.get("currency") or (v.get("currency") if isinstance(v, dict) else None) or ""
    if isinstance(v, dict):
        lo, hi, one = v.get("minValue"), v.get("maxValue"), v.get("value")
        period = _UNIT.get(str(v.get("unitText", "")).upper())
        if lo or hi:
            nums = {"salary_min": _num(lo or hi), "salary_max": _num(hi or lo), "salary_currency": cur or None, "salary_period": period}
            return f"{lo}-{hi} {cur}".strip(), nums
        if one:
            return str(one), {"salary_min": _num(one), "salary_max": _num(one), "salary_currency": cur or None, "salary_period": period}
    elif isinstance(v, (int, float)):
        return f"{v} {cur}".strip(), {"salary_min": float(v), "salary_max": float(v), "salary_currency": cur or None}
    return None, {}


def _num(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _remote(p: dict) -> bool:
    t = p.get("jobLocationType")
    return "TELECOMMUTE" in (t if isinstance(t, list) else [t])


def extract_jobs(html: str, page_url: str, source: str) -> list[Job]:
    jobs: list[Job] = []
    for block in Selector(html).css('script[type="application/ld+json"]::text').getall():
        try:
            data = json.loads(block.strip().rstrip(";").strip())   # OCC cierra el JSON con ";"
        except json.JSONDecodeError:
            continue
        for p in _iter_postings(_lower_keys(data)):
            org = p.get("hiringOrganization")
            salary_text, salary_fields = _salary(p)
            url = p.get("url") or page_url
            jobs.append(Job(
                title=" ".join(str(p.get("title", "")).split()),
                company=(_flat(org) or [""])[0],
                location=_location(p),
                remote=_remote(p),
                work_mode="remote" if _remote(p) else None,
                description=clean_html(p.get("description", "")),
                raw={"html": p.get("description", "")},
                url=url,
                source=source,
                posted_at=parse_date(p.get("datePosted")),
                expires_at=parse_date(p.get("validThrough")),
                employment_type=normalize_employment(p.get("employmentType")),
                salary=salary_text,
                apply_url=url if str(p.get("directApply")).lower() == "true" else None,
                **{k: v for k, v in salary_fields.items() if v is not None},
            ))
    return [j for j in jobs if j.title]
