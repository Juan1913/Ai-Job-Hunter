"""Detección de idioma (ES/EN) sin dependencias: puntaje por palabras funcionales frecuentes.
Suficiente para decidir el idioma de una oferta o un CV; ante duda devuelve el valor por defecto."""
import re

from app.text import norm

_ES = set("el la los las de del y en con para por una un que se es al su sus como más mas nuestro nuestra "
          "equipo experiencia trabajo requisitos buscamos ofrecemos conocimientos años anos empresa "
          "tenemos serás seras puesto vacante funciones responsabilidades contrato salario habilidades "
          "deseable manejo sobre entre desde hasta nuestros también tambien".split())
_EN = set("the and of to in for with a an is are be our we you your will as on at by or this that from "
          "team experience work requirements looking offer knowledge years company role position "
          "responsibilities skills required preferred about including must have has able strong "
          "ability join build working across within their".split())

def lang_scores(text: str) -> tuple[float, float]:
    toks = re.findall(r"[a-záéíóúñü]+", (text or "").lower())
    if not toks:
        return 0.0, 0.0
    es = sum(1 for t in toks if norm(t) in _ES or t in _ES)
    en = sum(1 for t in toks if t in _EN)
    es += 2 * len(re.findall(r"[ñ¿¡]|ción\b|ciones\b|mente\b", (text or "").lower()))
    en += 2 * len(re.findall(r"\b\w+(?:tion|ing|ment)s?\b", (text or "").lower())) // 2
    n = len(toks)
    return es / n, en / n

def detect_lang(text: str, default: str = "es") -> str:
    """'es' | 'en'."""
    es, en = lang_scores(text)
    if es == en:
        return default
    return "es" if es > en else "en"

def detect_lang_confidence(text: str, default: str = "es") -> tuple[str, float]:
    es, en = lang_scores(text)
    if es + en == 0:
        return default, 0.0
    lang = detect_lang(text, default)
    return lang, round(abs(es - en) / (es + en), 2)

# Encabezados estándar que reconocen los ATS, por idioma. La clave es la sección canónica.
HEADINGS = {
    "es": {"summary": "RESUMEN PROFESIONAL", "skills": "HABILIDADES", "experience": "EXPERIENCIA LABORAL",
           "education": "EDUCACIÓN", "certifications": "CERTIFICACIONES", "languages": "IDIOMAS", "contact": "CONTACTO",
           "present": "Actualidad"},
    "en": {"summary": "PROFESSIONAL SUMMARY", "skills": "SKILLS", "experience": "WORK EXPERIENCE",
           "education": "EDUCATION", "certifications": "CERTIFICATIONS", "languages": "LANGUAGES", "contact": "CONTACT",
           "present": "Present"},
}

def headings(lang: str) -> dict[str, str]:
    return HEADINGS.get(lang, HEADINGS["es"])
