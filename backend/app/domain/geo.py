"""Geografía para el enrutamiento de fuentes: cada país declara su moneda, idiomas y los
dominios de los portales que lo cubren. Los portales consultan `COUNTRIES` para elegir
el dominio correcto según el país del usuario (no hay que tocar el código de cada fuente)."""
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Country:
    code: str
    name: str                          # como lo escribe un humano / buscador
    languages: tuple[str, ...] = ("es",)
    currency: str = "USD"
    aliases: tuple[str, ...] = ()      # otros nombres para detectar el país en un texto
    # subdominios/dominios por portal; ausente = el portal no opera en ese país
    portals: dict[str, str] = field(default_factory=dict)


def _c(code, name, lang, cur, aliases=(), **portals) -> Country:
    return Country(code, name, tuple(lang), cur, tuple(aliases), portals)


COUNTRIES: dict[str, Country] = {c.code: c for c in [
    _c("CO", "Colombia", "es", "COP", ["bogotá", "bogota", "medellín", "medellin", "cali", "barranquilla", "cartagena", "bucaramanga"],
       computrabajo="co", indeed="co.indeed.com", magneto="co", elempleo="co", jobomas="co"),
    _c("MX", "México", "es", "MXN", ["mexico", "cdmx", "ciudad de méxico", "guadalajara", "monterrey", "nuevo león"],
       computrabajo="mx", indeed="mx.indeed.com", bumeran="www.bumeran.com.mx", occ="www.occ.com.mx"),
    _c("AR", "Argentina", "es", "ARS", ["buenos aires", "córdoba", "rosario", "caba"],
       computrabajo="ar", indeed="ar.indeed.com", zonajobs="www.zonajobs.com.ar", bumeran="www.bumeran.com.ar"),
    _c("CL", "Chile", "es", "CLP", ["santiago de chile", "valparaíso", "concepción"],
       computrabajo="cl", indeed="cl.indeed.com", laborum="www.laborum.cl"),
    _c("PE", "Perú", "es", "PEN", ["peru", "lima", "arequipa", "trujillo"],
       computrabajo="pe", indeed="pe.indeed.com", bumeran="www.bumeran.com.pe"),
    _c("EC", "Ecuador", "es", "USD", ["quito", "guayaquil", "cuenca"],
       computrabajo="ec", indeed="ec.indeed.com", bumeran="www.bumeran.com.ec"),
    _c("VE", "Venezuela", "es", "VES", ["caracas", "maracaibo", "valencia (ve)"],
       computrabajo="ve", indeed="ve.indeed.com"),
    _c("CR", "Costa Rica", "es", "CRC", ["san josé (cr)", "heredia", "alajuela"],
       computrabajo="cr", indeed="cr.indeed.com"),
    _c("PA", "Panamá", "es", "USD", ["panama", "ciudad de panamá"],
       computrabajo="pa", indeed="pa.indeed.com", bumeran="www.bumeran.com.pa"),
    _c("DO", "República Dominicana", "es", "DOP", ["santo domingo", "santiago de los caballeros"],
       computrabajo="do"),
    _c("GT", "Guatemala", "es", "GTQ", ["ciudad de guatemala"], computrabajo="gt"),
    _c("UY", "Uruguay", "es", "UYU", ["montevideo"], computrabajo="uy", indeed="uy.indeed.com"),
    _c("BO", "Bolivia", "es", "BOB", ["la paz", "santa cruz de la sierra"], computrabajo="bo"),
    _c("HN", "Honduras", "es", "HNL", ["tegucigalpa", "san pedro sula"], computrabajo="hn"),
    _c("SV", "El Salvador", "es", "USD", ["san salvador"], computrabajo="sv"),
    _c("NI", "Nicaragua", "es", "NIO", ["managua"], computrabajo="ni"),
    _c("PY", "Paraguay", "es", "PYG", ["asunción", "asuncion"], computrabajo="py"),
    _c("ES", "España", "es", "EUR", ["spain", "madrid", "barcelona", "valencia", "sevilla"], indeed="es.indeed.com"),
    _c("BR", "Brasil", "pt", "BRL", ["brazil", "são paulo", "sao paulo", "rio de janeiro"], indeed="br.indeed.com"),
    _c("US", "United States", "en", "USD", ["usa", "u.s.", "estados unidos", "new york", "california", "texas"], indeed="www.indeed.com"),
    _c("CA", "Canada", "en", "CAD", ["canadá", "toronto", "vancouver"], indeed="ca.indeed.com"),
    _c("GB", "United Kingdom", "en", "GBP", ["uk", "reino unido", "london", "england"], indeed="uk.indeed.com"),
    _c("DE", "Germany", "de", "EUR", ["alemania", "deutschland", "berlin", "münchen", "munich"], indeed="de.indeed.com"),
    _c("PT", "Portugal", "pt", "EUR", ["lisboa", "lisbon", "porto"], indeed="pt.indeed.com"),
    _c("FR", "France", "fr", "EUR", ["francia", "paris"], indeed="fr.indeed.com"),
    _c("IT", "Italy", "it", "EUR", ["italia", "roma", "milano"], indeed="it.indeed.com"),
]}

# Trabajos Diarios: un subdominio por país (robots.txt: Crawl-delay 2 s; verificado 2026-11-04)
for _code, _host in {"AR": "ar.trabajosdiarios.com", "BO": "bo.trabajosdiarios.com", "CL": "cl.trabajosdiarios.com", "CO": "co.trabajosdiarios.com",
                     "CR": "trabajosdiarios.co.cr", "EC": "ec.trabajosdiarios.com", "SV": "sv.trabajosdiarios.com", "ES": "es.trabajosdiarios.com",
                     "GT": "gt.trabajosdiarios.com", "HN": "hn.trabajosdiarios.com", "MX": "mx.trabajosdiarios.com", "PA": "pa.trabajosdiarios.com",
                     "PY": "py.trabajosdiarios.com", "PE": "pe.trabajosdiarios.com", "DO": "do.trabajosdiarios.com", "UY": "uy.trabajosdiarios.com",
                     "VE": "ve.trabajosdiarios.com"}.items():
    COUNTRIES[_code].portals["trabajosdiarios"] = _host

# Compatibilidad: el resto del código importa COUNTRY_NAMES
COUNTRY_NAMES = {code: c.name for code, c in COUNTRIES.items()}

LATAM = ["CO", "MX", "AR", "CL", "PE", "EC", "VE", "CR", "PA", "DO", "GT", "UY", "BO", "HN", "SV", "NI", "PY", "BR"]
DEFAULT_COUNTRY = "CO"

# Códigos de moneda por símbolo ambiguo ("$") se resuelven con el país
CURRENCY_CODES = {c.currency for c in COUNTRIES.values()} | {"USD", "EUR", "GBP"}


def place_names(regions: list[str], countries: list[str]) -> str:
    """Ubicación legible para los buscadores (los códigos ISO se confunden: CO = Colorado)."""
    return ", ".join(regions or [COUNTRY_NAMES.get(c, c) for c in countries])


def country_name(code: str | None) -> str:
    return COUNTRY_NAMES.get((code or "").upper(), code or "")


def portal_hosts(portal: str) -> dict[str, str]:
    """País ISO -> host/subdominio del portal (solo países donde opera)."""
    return {code: c.portals[portal] for code, c in COUNTRIES.items() if portal in c.portals}


def countries_for(portal: str) -> set[str]:
    return set(portal_hosts(portal))


def default_currency(country: str | None) -> str | None:
    c = COUNTRIES.get((country or "").upper())
    return c.currency if c else None


def country_languages(country: str | None) -> tuple[str, ...]:
    c = COUNTRIES.get((country or "").upper())
    return c.languages if c else ()


def guess_country(text: str) -> str | None:
    """Intenta deducir el país ISO desde una ubicación libre ("Medellín, Antioquia, Colombia", "Bogota, D.C., CO")."""
    low = (text or "").lower()
    if not low.strip():
        return None
    for code, c in COUNTRIES.items():          # nombre oficial / alias explícito
        names = (c.name.lower(), *c.aliases)
        if any(_word(low, n) for n in names):
            return code
    for token in (t.strip(" .") for t in text.replace("/", ",").split(",")):
        if token.upper() in COUNTRIES and len(token) == 2:     # ", CO" al final de un address JSON-LD
            return token.upper()
    return None


def _word(low: str, needle: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(needle)}(?![\w])", low) is not None


def effective_countries(query_countries: list[str], supported: set[str] | dict, default: str = DEFAULT_COUNTRY) -> list[str]:
    """Países sobre los que debe buscar una fuente: la intersección con lo que el usuario pidió.
    Sin países pedidos -> el país por defecto del producto (si la fuente lo cubre) o el primero que cubra."""
    sup = set(supported)
    if query_countries:
        return [c for c in query_countries if c in sup]
    if not sup:
        return []
    return [default] if default in sup else [sorted(sup)[0]]
