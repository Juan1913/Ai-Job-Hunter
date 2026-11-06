"""Empresas semilla GLOBALES (tabla Company con user_id NULL). Fuente única de verdad: la usan
`registry.build_sources()` cuando no hay filas en BD y `scripts/seed_companies.py` para sembrar la tabla.

Solo entran tableros verificados en vivo (fecha en `verified`). Candidatas descartadas y por qué: ver `DISCARDED`."""

VERIFIED = "2026-10-07"

SEED_COMPANIES: list[dict] = [
    # --- Colombia / LATAM: bancos y corporativos
    {"company": "Bancolombia", "kind": "successfactors", "url": "https://empleo.grupobancolombia.com/bancolombia",
     "note": "SAP SuccessFactors (Career Site Builder). Portal real: empleo.grupobancolombia.com/bancolombia"},
    {"company": "MercadoLibre", "kind": "careers", "url": "https://careers-meli.mercadolibre.com/en/positions", "stealth": True,
     "note": "Sitio propio (detrás hay Eightfold); requiere StealthyFetcher"},
    {"company": "Rappi", "kind": "careers", "url": "https://careers.rappi.com/es/careers", "stealth": True,
     "note": "Next.js propio; listado /es/careers y detalle /es/careers/{uuid} renderizados con StealthyFetcher"},
    # --- fintech / tecnología LATAM (APIs de ATS estables)
    {"company": "Nubank", "kind": "ashby", "token": "nubank"},
    {"company": "Addi", "kind": "ashby", "token": "addi"},
    {"company": "Belvo", "kind": "ashby", "token": "belvo"},
    {"company": "Kueski", "kind": "ashby", "token": "kueski"},
    {"company": "Despegar", "kind": "lever", "token": "despegar"},
    {"company": "Kavak", "kind": "lever", "token": "kavak"},
    {"company": "dLocal", "kind": "lever", "token": "dlocal"},
    {"company": "Fintual", "kind": "lever", "token": "fintual"},
    {"company": "CI&T", "kind": "lever", "token": "ciandt"},
    {"company": "Cabify", "kind": "greenhouse", "token": "cabify"},
    {"company": "Clara", "kind": "greenhouse", "token": "clara"},
    {"company": "VTEX", "kind": "greenhouse", "token": "vtex"},
    {"company": "Wizeline", "kind": "greenhouse", "token": "wizeline"},
    {"company": "Thoughtworks", "kind": "greenhouse", "token": "thoughtworks"},
    {"company": "Platzi", "kind": "workable", "token": "platzi"},
    {"company": "Endava", "kind": "smartrecruiters", "token": "endava"},
]

DISCARDED = {
    "Davivienda": "página de empleo sin ATS público detectable (sin JSON-LD ni API); no verificado",
    "Globant": "career.globant.com usa SuccessFactors pero /search devuelve 404 (sitio JS); no verificado",
    "Bold (Colombia)": "el token 'bold' de Greenhouse pertenece a otra empresa (Bold Penalty, EE. UU.)",
    "Koombea": "su tablero SmartRecruiters solo tiene una oferta de prueba de 2020",
    "Glassdoor": "ToS restrictivo, enlaces con tracking y datos de oferta tras redirección; no integrado",
}
