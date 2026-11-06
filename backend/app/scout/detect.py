"""A partir de una URL de carreras, detecta el ATS (API estable) o devuelve 'careers' (página genérica)."""
from urllib.parse import urlsplit


def detect_kind(url: str) -> tuple[str, str | None]:
    p = urlsplit(url)
    host, parts = (p.hostname or "").lower(), [x for x in p.path.split("/") if x]
    sub = host.split(".")[0]
    if host.endswith("greenhouse.io") and parts:
        return "greenhouse", parts[0]
    if host in ("jobs.lever.co", "jobs.eu.lever.co") and parts:
        return "lever", parts[0]
    if host == "jobs.ashbyhq.com" and parts:
        return "ashby", parts[0]
    if host in ("jobs.smartrecruiters.com", "careers.smartrecruiters.com") and parts:
        return "smartrecruiters", parts[0]
    if host == "apply.workable.com" and parts:
        return "workable", parts[0]
    if host.endswith(".myworkdayjobs.com"):
        from app.scout.sources.ats import Workday
        token = Workday.token_from_url(url)
        if token:
            return "workday", token
    if host.endswith(".teamtailor.com") or host.endswith(".teamtailor.eu"):
        return "teamtailor", host
    if host.endswith(".bamboohr.com") and sub not in ("www", "app"):
        return "bamboohr", sub
    if host.endswith(".recruitee.com") and sub not in ("www", "app"):
        return "recruitee", sub
    return "careers", None
