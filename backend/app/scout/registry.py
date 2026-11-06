"""Catálogo de fuentes. `build_sources()` arma la lista que usa el runner:
fuentes globales (feeds/portales con enrutamiento por país) + empresas (tabla Company o semillas)."""
from app.config import settings
from app.scout.seeds import SEED_COMPANIES
from app.scout.sources.api_sources import (
    Adzuna,
    Arbeitnow,
    GetOnBoard,
    Greenhouse,
    HNHiring,
    InfoJobs,
    Jobicy,
    Jooble,
    Lever,
    RemoteOK,
    Remotive,
    WeWorkRemotely,
    WorkingNomads,
)
from app.scout.sources.ats import Ashby, BambooHR, Recruitee, SmartRecruiters, SuccessFactors, Teamtailor, Workable, Workday
from app.scout.sources.careers import CareerPage
from app.scout.sources.indeed import IndeedSource
from app.scout.sources.latam import NAVENT_PORTALS, NaventSource, OCCSource
from app.scout.sources.linkedin import LinkedInSource
from app.scout.sources.portals import PORTALS, PortalSource

# Compatibilidad: antes COMPANIES era una lista local; ahora vive en seeds.py
COMPANIES = [{"company": c["company"], "kind": c["kind"], "token": c.get("token"), "url": c.get("url"), "stealth": c.get("stealth", False)}
             for c in SEED_COMPANIES]

# kind -> clase de tablero que se construye con (token, empresa)
TOKEN_KINDS = {"greenhouse": Greenhouse, "lever": Lever, "ashby": Ashby, "smartrecruiters": SmartRecruiters, "workable": Workable,
               "workday": Workday, "teamtailor": Teamtailor, "bamboohr": BambooHR, "recruitee": Recruitee}


def keyed_sources() -> list:
    """APIs con clave gratuita: solo existen si hay clave en la configuración (JH_ADZUNA_*, JH_JOOBLE_API_KEY, JH_INFOJOBS_*)."""
    out: list = []
    if settings.adzuna_app_id and settings.adzuna_app_key:
        out.append(Adzuna(settings.adzuna_app_id, settings.adzuna_app_key))
    if settings.jooble_api_key:
        out.append(Jooble(settings.jooble_api_key))
    if settings.infojobs_client_id and settings.infojobs_client_secret:
        out.append(InfoJobs(settings.infojobs_client_id, settings.infojobs_client_secret))
    return out


def global_sources() -> list:
    """Fuentes que no dependen de una empresa concreta."""
    return [Remotive(), Arbeitnow(), RemoteOK(), WeWorkRemotely(), Jobicy(), WorkingNomads(), HNHiring(), GetOnBoard(),
            *(PortalSource(c) for c in PORTALS), IndeedSource(), LinkedInSource(),
            *(NaventSource(p) for p in NAVENT_PORTALS), OCCSource(), *keyed_sources()]   # agregadores al final: en un duplicado gana la fuente original


def company_source(c: dict):
    kind, name = c["kind"], c["company"]
    if kind in TOKEN_KINDS and c.get("token"):
        return TOKEN_KINDS[kind](c["token"], name)
    if kind == "successfactors" and (c.get("url") or c.get("token")):
        return SuccessFactors(c.get("url") or c["token"], name)
    if c.get("url"):
        return CareerPage(name, c["url"], c.get("stealth", False))
    return None


def build_sources(companies: list[dict] | None = None):
    """companies: filas de la tabla Company (dict). Si es None se usan las semillas globales."""
    sources = global_sources()
    for c in (COMPANIES if companies is None else companies):
        src = company_source(c)
        if src is not None:
            sources.append(src)
    return sources
