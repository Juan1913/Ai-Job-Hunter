"""Último paso del CV adaptado: idioma de la oferta. Fija el idioma, traduce (con guardias) si hace falta,
regenera el texto con encabezados del idioma y vuelve a pasar el verificador ATS."""
from __future__ import annotations

import json
import re
from collections import Counter

from app.ai.report import usable
from app.ats.checker import check
from app.cv.audit import audit_and_repair, audit_cv
from app.cv.lang import detect_lang
from app.cv.render import render_text, resolve_lang
from app.cv.templates import DEFAULT_TEMPLATE
from app.domain.job import Job
from app.domain.profile import Profile
from app.domain.tailoring import KeywordGap, TailoredCV, includable
from app.text import contains, norm

TRANSLATE_SYS = """Eres un traductor de CVs. Traduce al {lang_name} cada texto de la lista "textos", uno por uno y en el mismo orden.
REGLAS: traduce solo el sentido; NO cambies cifras, porcentajes, siglas, nombres de herramientas, empresas ni cargos propios;
NO añadas información; devuelve exactamente la misma cantidad de textos.
Devuelve SOLO JSON: {{"textos": ["", ""]}}"""

_TECH = re.compile(r"\b(?:[A-Z]{2,}[A-Za-z0-9+#&/.\-]*|[A-Za-z]+[A-Z][A-Za-z0-9]*|[A-Za-z0-9]+[+#&][A-Za-z0-9+#&]*)\b")

# Siglas que sí cambian al traducir (ES <-> EN)
_ACRO = {"TI": {"IT"}, "IT": {"TI"}, "RRHH": {"HR"}, "HR": {"RRHH"}, "IA": {"AI"}, "AI": {"IA"}, "PYME": {"SME"}, "PYMES": {"SME", "SMES", "SMEs"}, "EEUU": {"US", "USA"},
         "SME": {"PYME"}, "SMEs": {"PYMES"}, "BD": {"DB"}, "DB": {"BD"}, "ONU": {"UN"}, "UN": {"ONU"}, "UE": {"EU"}, "EU": {"UE"}}

def _protected(text: str) -> set[str]:
    """Siglas y herramientas con forma de marca (AWS, PostgreSQL, C++, MITRE ATT&CK) que la traducción debe conservar."""
    return set(_TECH.findall(text))

def _digits(text: str) -> Counter:
    """Cifras sin separadores de miles: '2.800' y '2,800' son la misma cifra."""
    return Counter(re.sub(r"(?<=\d)[.,](?=\d{3}\b)", "", m) for m in re.findall(r"\d+(?:[.,]\d+)*", text))

def _segments(cv: TailoredCV) -> list[str]:
    """Titular, resumen y todas las viñetas, en orden, como lista plana (los modelos pequeños la siguen mejor que un JSON anidado)."""
    return [cv.headline, cv.summary, *[b for e in cv.experience for b in e.get("bullets", [])]]

def validate_translation(orig: list[str], new: object, gaps: list[KeywordGap]) -> list[str]:
    """Problemas de una traducción: cantidad de textos, cifras, siglas/herramientas/nombres y palabras clave prohibidas."""
    if not isinstance(new, dict) or not isinstance(new.get("textos"), list) or not all(isinstance(x, str) for x in new["textos"]):
        return ["la respuesta no es un JSON con la lista 'textos'"]
    t = new["textos"]
    if len(t) != len(orig):
        return [f"se esperaban {len(orig)} textos y llegaron {len(t)}"]
    issues: list[str] = []
    for i, (a, b) in enumerate(zip(orig, t)):
        if bool(a.strip()) != bool(b.strip()):
            issues.append(f"el texto {i + 1} quedó vacío o se inventó")
        elif _digits(a) != _digits(b):
            issues.append(f"las cifras del texto {i + 1} no coinciden con el original")
    nn = norm("\n".join(t))
    src_tok, new_tok = _protected("\n".join(orig)), _protected("\n".join(t))
    for tok in src_tok:
        if not contains(nn, tok) and not any(contains(nn, alt) for alt in _ACRO.get(tok, ())):
            issues.append(f"se perdió o alteró '{tok}'")
    for tok in new_tok - src_tok:                       # siglas/herramientas nuevas = invención (salvo equivalentes ES<->EN)
        if not any(tok in _ACRO.get(x, ()) for x in src_tok):
            issues.append(f"se añadió '{tok}', que no estaba en el original")
    for g in gaps:
        if not includable(g) and contains(nn, g.keyword):
            issues.append(f"aparece '{g.keyword}', que el candidato no ha confirmado")
    return issues

def translate_cv(cv: TailoredCV, target: str, writer, gaps: list[KeywordGap], attempts: int = 2) -> tuple[TailoredCV, str]:
    """Traduce titular/resumen/viñetas. Si el modelo falla o rompe una guardia se conserva el original. Devuelve (cv, nota)."""
    orig = _segments(cv)
    if not any(x.strip() for x in orig):
        return cv, "sin contenido que traducir"
    name = "inglés" if target == "en" else "español"
    payload = json.dumps({"textos": orig}, ensure_ascii=False)
    feedback = ""
    last: list[str] = []
    for _ in range(attempts):
        try:
            new = writer.complete_json(TRANSLATE_SYS.format(lang_name=name), payload + feedback)
        except Exception as e:  # noqa: BLE001
            return cv, f"traducción omitida: {e}"
        last = validate_translation(orig, new, gaps)
        if not last:
            t = [x.strip() for x in new["textos"]]
            out = cv.model_copy(deep=True)
            out.headline, out.summary = t[0] or cv.headline, t[1] or cv.summary
            k = 2
            for e in out.experience:
                n = len(e.get("bullets", []))
                e["bullets"] = t[k:k + n]
                k += n
            return out, f"traducido a {target}"
        feedback = "\n\nCORRIGE: " + "; ".join(last)
    return cv, "traducción descartada por las guardias (" + "; ".join(last[:3]) + ")"

def finalize_cv(cv: TailoredCV, job: Job, gaps: list[KeywordGap], writer=None, lang: str | None = None, profile: Profile | None = None) -> TailoredCV:
    """Idioma objetivo = el de la oferta (o `lang`). Con `profile`: auditoría de fidelidad (repara lo inventado, o marca 'requiere revisión').
    Re-renderiza el texto, recalcula el reporte ATS y lo verifica sobre el PDF real (lo que verá el escáner)."""
    target = lang if lang in ("es", "en") else detect_lang(f"{job.title}. {job.description[:3000]}", "es")
    note = None
    notes: list[str] = []
    had_writer = writer is not None
    writer = usable(writer)
    if had_writer and writer is None and lang not in ("es", "en") and resolve_lang(cv) != target:
        # Sin IA no se traduce: el CV conserva el idioma de su contenido (encabezados incluidos) en vez de mezclar idiomas.
        notes.append(f"sin IA no se traduce: el CV queda en {resolve_lang(cv)} (la oferta está en {target})")
        target = resolve_lang(cv)
    if profile is not None:                       # antes de traducir: el borrador está en el idioma del perfil y el solape léxico es fiable
        cv, rep = audit_and_repair(cv, profile, gaps, job, llm=writer)
        notes += [f"auditoría: {a}" for a in rep.repaired]
    if writer is not None and resolve_lang(cv) != target:
        cv, note = translate_cv(cv, target, writer, gaps)
    cv.lang = target
    if profile is not None:
        rep = audit_cv(cv, profile, gaps, job, lexical=not (note or "").startswith("traducido"))
        rep.repaired = [a for a in notes]
        cv.audit, cv.review_required = rep.compact(), rep.requires_review
        if rep.requires_review:
            notes.append("requiere revisión: " + " | ".join(rep.unsupported_high[:3]))
    cv.text = render_text(cv, target)
    cv.ats = check(cv.text, gaps, target)
    if profile is not None:
        notes += _verify_artifact(cv, gaps, target)
    if note:
        cv.trace = [*cv.trace, note]
    cv.trace = [*cv.trace, *notes]
    return cv

def _verify_artifact(cv: TailoredCV, gaps: list[KeywordGap], lang: str) -> list[str]:
    """Puntúa el PDF real (capa de texto extraída). No debe romper la entrega si el extractor falla."""
    try:
        from app.cv.artifact import verify_artifact
        rep = verify_artifact(cv, gaps, DEFAULT_TEMPLATE, "pdf", lang)
    except Exception as e:  # noqa: BLE001
        return [f"verificación del archivo omitida: {e}"]
    cv.artifact = rep.model_dump(mode="json")
    if cv.ats:
        cv.ats.artifact_score = rep.artifact_score
    return [] if rep.ok else ["archivo: " + "; ".join(rep.issues[:2])]
