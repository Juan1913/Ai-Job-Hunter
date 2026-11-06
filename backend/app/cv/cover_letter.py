"""Carta de presentación con las mismas guardias anti-invención que el CV.

El modelo solo recibe hechos del perfil y las palabras clave PERMITIDAS; después se valida: nada prohibido,
ninguna cifra ni nombre propio que no esté en el perfil o la oferta, sin marcadores de plantilla. Si el
modelo no cumple (o no responde) se entrega una carta determinista construida solo con datos del perfil."""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.ai.privacy import profile_for_prompt
from app.ai.report import usable
from app.ats.gaps import profile_corpus
from app.cv.lang import detect_lang
from app.domain.job import Job
from app.domain.profile import Profile
from app.domain.tailoring import GapStatus, KeywordGap, includable
from app.profile import dates
from app.safety.integrity import UNTRUSTED_RULE, offer_block
from app.text import clean_title, contains, norm

SYSTEM = """Eres un redactor de cartas de presentación. Escribe en {lang_name} una carta breve (170 a 250 palabras, 3 párrafos).
REGLAS ESTRICTAS:
1. Usa SOLO hechos del perfil. No inventes empresas, cargos, cifras, años, logros ni herramientas.
2. Menciona de forma natural algunas "PALABRAS CLAVE PERMITIDAS" que estén respaldadas por el perfil.
3. NO uses ninguna palabra de "PROHIBIDAS": el candidato no las tiene.
4. Sin marcadores como [Nombre] ni {{empresa}}. Empieza con el saludo y termina con una despedida SIN firmar (la firma se añade aparte).
Devuelve SOLO JSON: {{"letter": "texto de la carta con párrafos separados por una línea en blanco"}}"""
SYSTEM += UNTRUSTED_RULE

class CoverLetter(BaseModel):
    text: str
    lang: str
    source: str = "llm"                      # llm | template
    issues: list[str] = Field(default_factory=list)   # problemas que quedaron (vacío = limpia)
    trace: list[str] = Field(default_factory=list)

_PLACEHOLDER = re.compile(r"\[[^\]]{2,40}\]|\{\{.*?\}\}|lorem ipsum|\bXXX+\b", re.I)
_CAP = re.compile(r"(?<![.!?¡¿\n:]\s)(?<!^)\b([A-ZÁÉÍÓÚÑ][\wáéíóúñ&+#.\-]{2,})")
_GENERIC_CAPS = set(map(norm, """Estimado Estimada Estimados Estimadas Dear Sincerely Regards Atentamente Cordialmente Saludos Gracias Thank Best Kind Respectfully
Hola Hello Hi Lunes Martes Mayo Junio Julio Agosto Enero Febrero Marzo Abril Equipo Team Recursos Humanos Human Resources Resources Reclutamiento Seleccion
Talent Talento Gerente Manager Director Directora""".split()))

def _facts_text(profile: Profile, job: Job) -> str:
    return norm(profile_corpus(profile) + " " + profile.full_name + " " + " ".join(profile.languages) + " " + profile.location.__str__() + " "
                + " ".join(f"{e.title} {e.company}" for e in profile.experience) + " "
                + " ".join(f"{e.degree} {e.institution}" for e in profile.education) + " " + job.company + " " + job.title + " " + job.description)

def validate_letter(text: str, profile: Profile, job: Job, gaps: list[KeywordGap], lang: str) -> list[str]:
    issues: list[str] = []
    n = norm(text)
    for g in gaps:
        if not includable(g) and contains(n, g.keyword):
            issues.append(f"menciona '{g.keyword}', que el candidato no ha confirmado")
    facts = _facts_text(profile, job)
    fact_nums = set(re.findall(r"\d+(?:[.,]\d+)*", facts))
    for num in set(re.findall(r"\d+(?:[.,]\d+)*", text)):
        if num not in fact_nums:
            issues.append(f"cifra '{num}' que no está en el perfil ni en la oferta")
    for m in _CAP.finditer(text):
        w = m.group(1).strip(".,;:")
        if norm(w) not in _GENERIC_CAPS and norm(w) not in facts and len(w) > 2:
            issues.append(f"menciona '{w}', que no está en el perfil ni en la oferta")
    if _PLACEHOLDER.search(text):
        issues.append("contiene marcadores de plantilla sin rellenar")
    words = len(text.split())
    if words < 80:
        issues.append(f"carta demasiado corta ({words} palabras)")
    if words > 380:
        issues.append(f"carta demasiado larga ({words} palabras)")
    if detect_lang(text, lang) != lang:
        issues.append("no está escrita en el idioma de la oferta")
    return issues

_EMPTY_GREETING = re.compile(r"^\s*(?:estimad[oa]s?(?:/a)?|dear|hola|hello)\s*[,:.]?\s*$", re.I)

def fix_greeting(text: str, job: Job, lang: str) -> str:
    """'Estimado/a ,' (nombre sin rellenar) -> saludo genérico al equipo de la empresa."""
    first, _, rest = text.partition("\n")
    if not _EMPTY_GREETING.match(first):
        return text
    company = job.company or ("su empresa" if lang == "es" else "your company")
    return (f"Estimado equipo de {company}:" if lang == "es" else f"Dear {company} hiring team,") + "\n" + rest

def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]

def _drop_violations(text: str, profile: Profile, job: Job, gaps: list[KeywordGap], lang: str) -> str:
    """Quita (por oración) lo que no pasa las guardias de hechos."""
    paras = []
    for para in text.split("\n\n"):
        keep = [s for s in _sentences(para) if not [i for i in validate_letter(s, profile, job, gaps, lang) if "demasiado" not in i and "idioma" not in i]]
        if keep:
            paras.append(" ".join(keep))
    return "\n\n".join(paras)

def _backed_keywords(gaps: list[KeywordGap]) -> list[str]:
    order = {GapStatus.HAVE: 0, GapStatus.ADJACENT: 1, GapStatus.MISSING: 2}
    return [g.keyword for g in sorted((g for g in gaps if includable(g)), key=lambda g: order[g.status])]

def template_letter(profile: Profile, job: Job, gaps: list[KeywordGap], lang: str) -> str:
    """Carta determinista: solo datos del perfil y palabras clave respaldadas."""
    title = clean_title(job.title)
    name = profile.full_name or ""
    kws = [k for k in _backed_keywords(gaps) if norm(k) != norm(title)][:5]
    last = max(profile.experience, key=lambda e: dates.parse_date(e.start) or "", default=None)
    es = lang == "es"
    company = job.company or ("su empresa" if es else "your company")
    who = profile.headline or (last.title if last else "")
    if es:
        p1 = f"Me dirijo a ustedes para postularme al puesto de {title} en {company}." + (f" Soy {who}" + (f" y mi experiencia más reciente es como {last.title} en {last.company}." if last and last.company else ".") if who else "")
        p2 = ("Mi perfil se ajusta a lo que buscan: cuento con experiencia en " + _join(kws, "y") + ".") if kws else "Mi experiencia profesional se alinea con los requisitos del puesto."
        p3 = "Me gustaría ampliar mi candidatura en una entrevista. Agradezco su tiempo y quedo atento a su respuesta."
        return f"Estimado equipo de {company}:\n\n{p1}\n\n{p2}\n\n{p3}\n\nAtentamente,\n{name}".strip()
    p1 = f"I am writing to apply for the {title} position at {company}." + (f" I am {_a(who)}" + (f" and my most recent role was {last.title} at {last.company}." if last and last.company else ".") if who else "")
    p2 = ("My background matches what you are looking for: I have experience with " + _join(kws, "and") + ".") if kws else "My professional experience aligns with the requirements of the role."
    p3 = "I would welcome the chance to discuss my application in an interview. Thank you for your time and consideration."
    return f"Dear {company} hiring team,\n\n{p1}\n\n{p2}\n\n{p3}\n\nSincerely,\n{name}".strip()

def _a(role: str) -> str:
    return ("an " if role[:1].lower() in "aeiou" else "a ") + role

def _join(items: list[str], conj: str) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" {conj} " + items[-1]

def generate_cover_letter(profile: Profile, job: Job, gaps: list[KeywordGap], llm, lang: str | None = None, max_attempts: int = 2) -> CoverLetter:
    lang = lang if lang in ("es", "en") else detect_lang(f"{job.title}. {job.description[:3000]}", "es")
    allowed = [f"- {g.keyword}" + (f" (evidencia: {g.evidence})" if g.evidence else "") for g in gaps if includable(g)]
    banned = [g.keyword for g in gaps if not includable(g)]
    facts = profile_for_prompt(profile)
    base = (f"{offer_block(job, 2500)}\n\nPERFIL:\n{facts}\n\nPALABRAS CLAVE PERMITIDAS:\n" + "\n".join(allowed)
            + "\n\nPROHIBIDAS: " + (", ".join(banned) or "ninguna"))
    system = SYSTEM.format(lang_name="inglés" if lang == "en" else "español")
    trace: list[str] = []
    feedback = ""
    best: tuple[str, list[str]] | None = None
    if usable(llm) is None:                    # sin IA (modo none, sin consentimiento, presupuesto agotado): plantilla
        return CoverLetter(text=template_letter(profile, job, gaps, lang), lang=lang, source="template",
                           trace=["carta de plantilla (sin IA: solo datos del perfil)"])
    for i in range(1, max_attempts + 1):
        try:
            data = llm.complete_json(system, base + feedback)
            text = str(data.get("letter") or "").strip() if isinstance(data, dict) else ""
        except Exception as e:  # noqa: BLE001
            trace.append(f"intento {i}: el modelo falló ({e})")
            if "no disponible" in str(e).lower():
                break
            continue
        text = fix_greeting(_PLACEHOLDER.sub("", text).strip(), job, lang)
        if profile.full_name and norm(profile.full_name) not in norm(text[-120:]):
            text = text.rstrip() + f"\n\n{profile.full_name}"
        issues = validate_letter(text, profile, job, gaps, lang)
        trace.append(f"intento {i}: {len(issues)} problemas" + (f" -> {issues[:4]}" if issues else ""))
        if best is None or len(issues) < len(best[1]):
            best = (text, issues)
        if not issues:
            return CoverLetter(text=text, lang=lang, source="llm", trace=trace)
        feedback = "\n\nCORRIGE ESTOS PROBLEMAS DEL INTENTO ANTERIOR:\n" + "\n".join(issues[:6])
    if best:                                   # intentar rescatar lo bueno quitando oraciones que violan las guardias
        cleaned = _drop_violations(best[0], profile, job, gaps, lang)
        if profile.full_name and norm(profile.full_name) not in norm(cleaned[-120:]):
            cleaned = cleaned.rstrip() + f"\n\n{profile.full_name}"
        left = validate_letter(cleaned, profile, job, gaps, lang)
        if len(cleaned.split()) >= 80 and not left:
            trace.append("se eliminaron oraciones no respaldadas por el perfil")
            return CoverLetter(text=cleaned, lang=lang, source="llm", trace=trace)
    trace.append("carta de plantilla (solo datos del perfil)")
    text = template_letter(profile, job, gaps, lang)
    return CoverLetter(text=text, lang=lang, source="template", issues=[], trace=trace)
