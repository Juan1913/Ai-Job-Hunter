"""Gazetario embebido (sin llamadas externas): ciudad/país -> coordenadas. Cubre Latinoamérica, España, EE. UU./Canadá y las grandes capitales de Europa.
Las ofertas con un lugar que no está aquí quedan «sin ubicar» (nunca se inventa una coordenada)."""
from __future__ import annotations

import re
import unicodedata

# nombre normalizado -> (nombre a mostrar, lat, lon, país ISO-2)
_CITIES = {
    # Colombia
    "bogota": ("Bogotá", 4.711, -74.072, "CO"), "medellin": ("Medellín", 6.244, -75.581, "CO"), "cali": ("Cali", 3.451, -76.532, "CO"),
    "barranquilla": ("Barranquilla", 10.964, -74.796, "CO"), "cartagena": ("Cartagena", 10.391, -75.479, "CO"), "bucaramanga": ("Bucaramanga", 7.119, -73.122, "CO"),
    "pereira": ("Pereira", 4.813, -75.696, "CO"), "manizales": ("Manizales", 5.07, -75.514, "CO"), "cucuta": ("Cúcuta", 7.894, -72.508, "CO"),
    "ibague": ("Ibagué", 4.438, -75.232, "CO"), "santa marta": ("Santa Marta", 11.241, -74.199, "CO"), "armenia": ("Armenia", 4.534, -75.681, "CO"),
    "villavicencio": ("Villavicencio", 4.142, -73.627, "CO"), "envigado": ("Envigado", 6.17, -75.591, "CO"), "itagui": ("Itagüí", 6.185, -75.599, "CO"),
    "chia": ("Chía", 4.859, -74.059, "CO"), "soacha": ("Soacha", 4.579, -74.217, "CO"), "neiva": ("Neiva", 2.927, -75.282, "CO"), "pasto": ("Pasto", 1.214, -77.281, "CO"),
    # México
    "ciudad de mexico": ("Ciudad de México", 19.433, -99.133, "MX"), "cdmx": ("Ciudad de México", 19.433, -99.133, "MX"), "mexico city": ("Ciudad de México", 19.433, -99.133, "MX"),
    "guadalajara": ("Guadalajara", 20.659, -103.349, "MX"), "monterrey": ("Monterrey", 25.686, -100.316, "MX"), "puebla": ("Puebla", 19.041, -98.206, "MX"),
    "queretaro": ("Querétaro", 20.589, -100.389, "MX"), "tijuana": ("Tijuana", 32.515, -117.038, "MX"), "merida": ("Mérida", 20.967, -89.592, "MX"),
    "leon": ("León", 21.122, -101.686, "MX"), "cancun": ("Cancún", 21.161, -86.851, "MX"), "zapopan": ("Zapopan", 20.72, -103.391, "MX"),
    # Argentina, Chile, Perú, Uruguay y resto
    "buenos aires": ("Buenos Aires", -34.604, -58.382, "AR"), "caba": ("Buenos Aires", -34.604, -58.382, "AR"), "cordoba": ("Córdoba", -31.42, -64.188, "AR"), "rosario": ("Rosario", -32.947, -60.639, "AR"), "mendoza": ("Mendoza", -32.889, -68.845, "AR"),
    "santiago": ("Santiago", -33.449, -70.669, "CL"), "valparaiso": ("Valparaíso", -33.047, -71.612, "CL"), "concepcion": ("Concepción", -36.827, -73.05, "CL"),
    "lima": ("Lima", -12.046, -77.043, "PE"), "arequipa": ("Arequipa", -16.409, -71.537, "PE"), "trujillo": ("Trujillo", -8.112, -79.029, "PE"),
    "montevideo": ("Montevideo", -34.901, -56.164, "UY"), "asuncion": ("Asunción", -25.264, -57.576, "PY"), "la paz": ("La Paz", -16.5, -68.15, "BO"), "santa cruz de la sierra": ("Santa Cruz de la Sierra", -17.784, -63.182, "BO"),
    "quito": ("Quito", -0.18, -78.468, "EC"), "guayaquil": ("Guayaquil", -2.17, -79.922, "EC"), "caracas": ("Caracas", 10.481, -66.904, "VE"), "maracaibo": ("Maracaibo", 10.654, -71.64, "VE"),
    "panama": ("Ciudad de Panamá", 8.983, -79.517, "PA"), "ciudad de panama": ("Ciudad de Panamá", 8.983, -79.517, "PA"), "san jose": ("San José", 9.928, -84.091, "CR"),
    "guatemala": ("Ciudad de Guatemala", 14.634, -90.507, "GT"), "ciudad de guatemala": ("Ciudad de Guatemala", 14.634, -90.507, "GT"), "san salvador": ("San Salvador", 13.693, -89.219, "SV"),
    "tegucigalpa": ("Tegucigalpa", 14.072, -87.192, "HN"), "managua": ("Managua", 12.114, -86.236, "NI"), "santo domingo": ("Santo Domingo", 18.486, -69.931, "DO"), "san juan": ("San Juan", 18.466, -66.106, "PR"), "la habana": ("La Habana", 23.113, -82.366, "CU"),
    "sao paulo": ("São Paulo", -23.55, -46.633, "BR"), "rio de janeiro": ("Río de Janeiro", -22.907, -43.173, "BR"), "belo horizonte": ("Belo Horizonte", -19.917, -43.934, "BR"), "brasilia": ("Brasilia", -15.794, -47.882, "BR"), "curitiba": ("Curitiba", -25.43, -49.271, "BR"), "porto alegre": ("Porto Alegre", -30.035, -51.218, "BR"),
    # España y Portugal
    "madrid": ("Madrid", 40.417, -3.704, "ES"), "barcelona": ("Barcelona", 41.385, 2.173, "ES"), "valencia": ("Valencia", 39.47, -0.376, "ES"), "sevilla": ("Sevilla", 37.389, -5.984, "ES"), "bilbao": ("Bilbao", 43.263, -2.935, "ES"),
    "malaga": ("Málaga", 36.72, -4.42, "ES"), "zaragoza": ("Zaragoza", 41.649, -0.888, "ES"), "lisboa": ("Lisboa", 38.722, -9.139, "PT"), "lisbon": ("Lisboa", 38.722, -9.139, "PT"), "porto": ("Oporto", 41.158, -8.629, "PT"),
    # EE. UU. y Canadá
    "new york": ("Nueva York", 40.713, -74.006, "US"), "nueva york": ("Nueva York", 40.713, -74.006, "US"), "san francisco": ("San Francisco", 37.775, -122.419, "US"), "los angeles": ("Los Ángeles", 34.052, -118.244, "US"),
    "seattle": ("Seattle", 47.606, -122.332, "US"), "austin": ("Austin", 30.267, -97.743, "US"), "miami": ("Miami", 25.762, -80.192, "US"), "chicago": ("Chicago", 41.878, -87.63, "US"), "boston": ("Boston", 42.36, -71.059, "US"),
    "denver": ("Denver", 39.739, -104.99, "US"), "atlanta": ("Atlanta", 33.749, -84.388, "US"), "dallas": ("Dallas", 32.777, -96.797, "US"), "houston": ("Houston", 29.76, -95.37, "US"), "washington": ("Washington D. C.", 38.907, -77.037, "US"),
    "san diego": ("San Diego", 32.716, -117.161, "US"), "portland": ("Portland", 45.515, -122.679, "US"), "toronto": ("Toronto", 43.653, -79.383, "CA"), "vancouver": ("Vancouver", 49.283, -123.121, "CA"), "montreal": ("Montreal", 45.502, -73.567, "CA"),
    # Europa
    "london": ("Londres", 51.507, -0.128, "GB"), "londres": ("Londres", 51.507, -0.128, "GB"), "manchester": ("Mánchester", 53.481, -2.243, "GB"), "dublin": ("Dublín", 53.35, -6.26, "IE"),
    "paris": ("París", 48.857, 2.352, "FR"), "berlin": ("Berlín", 52.52, 13.405, "DE"), "munich": ("Múnich", 48.137, 11.576, "DE"), "hamburg": ("Hamburgo", 53.551, 9.994, "DE"), "frankfurt": ("Fráncfort", 50.11, 8.682, "DE"),
    "amsterdam": ("Ámsterdam", 52.37, 4.895, "NL"), "brussels": ("Bruselas", 50.85, 4.352, "BE"), "zurich": ("Zúrich", 47.377, 8.542, "CH"), "vienna": ("Viena", 48.208, 16.373, "AT"), "stockholm": ("Estocolmo", 59.329, 18.069, "SE"),
    "copenhagen": ("Copenhague", 55.676, 12.568, "DK"), "oslo": ("Oslo", 59.914, 10.752, "NO"), "helsinki": ("Helsinki", 60.17, 24.938, "FI"), "warsaw": ("Varsovia", 52.23, 21.012, "PL"), "prague": ("Praga", 50.075, 14.438, "CZ"),
    "milan": ("Milán", 45.464, 9.19, "IT"), "roma": ("Roma", 41.903, 12.496, "IT"), "rome": ("Roma", 41.903, 12.496, "IT"), "tel aviv": ("Tel Aviv", 32.085, 34.782, "IL"),
    # Otros polos
    "bangalore": ("Bangalore", 12.972, 77.594, "IN"), "bengaluru": ("Bangalore", 12.972, 77.594, "IN"), "singapore": ("Singapur", 1.352, 103.82, "SG"), "dubai": ("Dubái", 25.205, 55.271, "AE"), "sydney": ("Sídney", -33.869, 151.209, "AU"), "tokyo": ("Tokio", 35.676, 139.65, "JP"),
}
# país normalizado (nombre o ISO-2) -> (nombre, lat, lon, ISO-2): centroide, para ofertas que solo dicen el país
_COUNTRIES = {
    "colombia": ("Colombia", 4.57, -74.3, "CO"), "mexico": ("México", 23.63, -102.55, "MX"), "argentina": ("Argentina", -38.42, -63.62, "AR"), "chile": ("Chile", -35.68, -71.54, "CL"),
    "peru": ("Perú", -9.19, -75.02, "PE"), "uruguay": ("Uruguay", -32.52, -55.77, "UY"), "paraguay": ("Paraguay", -23.44, -58.44, "PY"), "bolivia": ("Bolivia", -16.29, -63.59, "BO"),
    "ecuador": ("Ecuador", -1.83, -78.18, "EC"), "venezuela": ("Venezuela", 6.42, -66.59, "VE"), "panama ": ("Panamá", 8.54, -80.78, "PA"), "costa rica": ("Costa Rica", 9.75, -83.75, "CR"),
    "guatemala ": ("Guatemala", 15.78, -90.23, "GT"), "el salvador": ("El Salvador", 13.79, -88.9, "SV"), "honduras": ("Honduras", 15.2, -86.24, "HN"), "nicaragua": ("Nicaragua", 12.87, -85.21, "NI"),
    "republica dominicana": ("República Dominicana", 18.74, -70.16, "DO"), "puerto rico": ("Puerto Rico", 18.22, -66.59, "PR"), "cuba": ("Cuba", 21.52, -77.78, "CU"), "brasil": ("Brasil", -14.24, -51.93, "BR"), "brazil": ("Brasil", -14.24, -51.93, "BR"),
    "espana": ("España", 40.46, -3.75, "ES"), "spain": ("España", 40.46, -3.75, "ES"), "portugal": ("Portugal", 39.4, -8.22, "PT"), "united states": ("Estados Unidos", 37.09, -95.71, "US"), "estados unidos": ("Estados Unidos", 37.09, -95.71, "US"),
    "usa": ("Estados Unidos", 37.09, -95.71, "US"), "canada": ("Canadá", 56.13, -106.35, "CA"), "united kingdom": ("Reino Unido", 55.38, -3.44, "GB"), "reino unido": ("Reino Unido", 55.38, -3.44, "GB"), "uk": ("Reino Unido", 55.38, -3.44, "GB"),
    "germany": ("Alemania", 51.17, 10.45, "DE"), "alemania": ("Alemania", 51.17, 10.45, "DE"), "france": ("Francia", 46.23, 2.21, "FR"), "francia": ("Francia", 46.23, 2.21, "FR"), "italy": ("Italia", 41.87, 12.57, "IT"), "italia": ("Italia", 41.87, 12.57, "IT"),
    "netherlands": ("Países Bajos", 52.13, 5.29, "NL"), "ireland": ("Irlanda", 53.41, -8.24, "IE"), "poland": ("Polonia", 51.92, 19.15, "PL"), "india": ("India", 20.59, 78.96, "IN"),
}
_ISO = {v[3]: v for v in _COUNTRIES.values()}
_REMOTE = re.compile(r"\b(remot[oe]|remote|anywhere|worldwide|teletrabajo|home office|work from home|global|latam|latin america)\b", re.I)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()


def is_remote_text(location: str) -> bool:
    return bool(_REMOTE.search(location or ""))


def locate(location: str, country: str | None = None) -> dict | None:
    """Mejor lugar conocido para una oferta: la ciudad si se reconoce; si no, el país. Devuelve None si no hay nada fiable."""
    tokens = [norm(t) for t in re.split(r"[,;/|·()\-–]+", location or "") if norm(t)]
    whole = norm(location or "")
    cc = (country or "").upper() or None

    def ok(hit) -> bool:        # «San José» o «Córdoba» existen en varios países: si la oferta dice el suyo, manda
        return cc is None or hit[3] == cc
    for t in [whole, *tokens]:                                   # ciudad primero
        hit = _CITIES.get(t)
        if hit and ok(hit):
            return {"place": hit[0], "lat": hit[1], "lon": hit[2], "country": hit[3], "precision": "city"}
    for t in tokens:                                             # «Medellín Antioquia»: ciudad al inicio del token
        for key in sorted(_CITIES, key=len, reverse=True):
            if len(key) > 3 and ok(_CITIES[key]) and re.search(rf"(^| ){re.escape(key)}( |$)", t):
                hit = _CITIES[key]
                return {"place": hit[0], "lat": hit[1], "lon": hit[2], "country": hit[3], "precision": "city"}
    for t in [*reversed(tokens), whole]:                         # luego país por nombre
        hit = _COUNTRIES.get(t) or _COUNTRIES.get(t + " ")
        if hit:
            return {"place": hit[0], "lat": hit[1], "lon": hit[2], "country": hit[3], "precision": "country"}
    if country and country.upper() in _ISO:
        hit = _ISO[country.upper()]
        return {"place": hit[0], "lat": hit[1], "lon": hit[2], "country": hit[3], "precision": "country"}
    return None
