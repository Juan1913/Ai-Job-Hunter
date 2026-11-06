"""Verificación del ARTEFACTO real (PDF/DOCX): lo que verá el escáner de verdad.

El texto lógico del CV (`cv.text`) no es lo que lee un ATS: este lee la capa de texto del archivo. Aquí se genera el archivo,
se extrae su texto con el MISMO extractor de la app (app/profile/extract.py), y se vuelve a puntuar con ats/checker.py sobre ESE texto.
Si difiere del lógico (palabras clave perdidas, secciones en otro orden, caracteres perdidos) se reporta y se prueba el resto de las
plantillas; se entrega la primera que conserva todo (se prefiere la predeterminada: Harvard)."""
from __future__ import annotations

import logging
from collections import Counter

from pydantic import BaseModel, Field

from app.ats.checker import check
from app.cv.render import render_docx, render_text, resolve_lang
from app.cv.templates import DEFAULT_TEMPLATE, TEMPLATES
from app.domain.tailoring import KeywordGap, TailoredCV, includable
from app.profile.extract import extract_document
from app.profile.sections import heading_of
from app.text import covered, norm

log = logging.getLogger(__name__)

MIN_FIDELITY = 0.97        # fracción de palabras del texto lógico que deben sobrevivir en la capa de texto


class ArtifactReport(BaseModel):
    format: str
    template: str                        # plantilla pedida
    template_used: str                   # la que se verificó/entregaría (puede diferir si se forzó una segura)
    forced_safe_template: bool = False
    ok: bool = True
    logical_score: int
    artifact_score: int                  # puntaje ATS sobre el texto extraído del archivo
    delta: int = 0                       # artifact_score - logical_score
    keyword_coverage_logical: float = 1.0
    keyword_coverage_artifact: float = 1.0
    lost_keywords: list[str] = Field(default_factory=list)
    order_ok: bool = True
    fidelity: float = 1.0
    lost_tokens: list[str] = Field(default_factory=list)
    pages: int = 1
    extraction_method: str = ""
    issues: list[str] = Field(default_factory=list)
    tried: list[dict] = Field(default_factory=list)      # intentos con otras plantillas: {template, ok, score}


def render_artifact(cv: TailoredCV, template: str, fmt: str, lang: str | None = None) -> bytes:
    if fmt == "pdf":
        from app.cv.pdf import render_pdf
        return render_pdf(cv, template, lang)
    if fmt == "docx":
        return render_docx(cv, template, lang)
    raise ValueError(f"formato desconocido: {fmt}")


def _section_order(text: str) -> list[str]:
    out: list[str] = []
    for ln in text.split("\n"):
        h = heading_of(ln)
        if h and h not in out:
            out.append(h)
    return out


def _tokens(text: str) -> Counter:
    import re
    return Counter(re.findall(r"[a-z0-9+#&]{2,}", norm(text)))


def _inspect(cv: TailoredCV, gaps: list[KeywordGap], template: str, fmt: str, lang: str, logical_text: str):
    data = render_artifact(cv, template, fmt, lang)
    ex = extract_document(f"cv.{fmt}", data)
    art_text = ex.text
    logical = check(logical_text, gaps, lang)
    art = check(art_text, gaps, lang)
    ln, an = norm(logical_text), norm(art_text)
    lost_kw = [g.keyword for g in gaps if includable(g) and covered(ln, g.keyword) and not covered(an, g.keyword)]
    lo, ao = _section_order(logical_text), _section_order(art_text)
    order_ok = [s for s in lo if s in ao] == [s for s in ao if s in lo] and set(lo) <= set(ao)
    lt, at = _tokens(logical_text), _tokens(art_text)
    total = sum(lt.values()) or 1
    missing = lt - at
    fidelity = 1 - sum(missing.values()) / total
    issues: list[str] = []
    if lost_kw:
        issues.append("el archivo pierde palabras clave que sí están en el texto lógico: " + ", ".join(lost_kw))
    if not order_ok:
        issues.append(f"las secciones salen en otro orden en el archivo (lógico {lo} / archivo {ao})")
    if fidelity < MIN_FIDELITY:
        issues.append(f"el archivo pierde {100 * (1 - fidelity):.1f}% de las palabras del texto lógico")
    if art.score < logical.score - 3:
        issues.append(f"el puntaje del archivo ({art.score}) es menor que el del texto lógico ({logical.score})")
    rep = ArtifactReport(format=fmt, template=template, template_used=template, ok=not issues, logical_score=logical.score, artifact_score=art.score,
                         delta=art.score - logical.score, keyword_coverage_logical=logical.keyword_coverage, keyword_coverage_artifact=art.keyword_coverage,
                         lost_keywords=lost_kw, order_ok=order_ok, fidelity=round(fidelity, 3), lost_tokens=sorted(missing)[:12], pages=ex.pages,
                         extraction_method=ex.method, issues=issues)
    return rep, art


def verify_artifact(cv: TailoredCV, gaps: list[KeywordGap], template: str | None = DEFAULT_TEMPLATE, fmt: str = "pdf", lang: str | None = None,
                    try_alternatives: bool = True) -> ArtifactReport:
    """Genera el archivo, extrae su capa de texto y la puntúa como lo haría un ATS. Si pierde algo prueba las otras plantillas
    y devuelve la primera que conserva todo (`forced_safe_template=True`); si ninguna, la mejor por puntaje con sus problemas."""
    tpl = template if template in TEMPLATES else DEFAULT_TEMPLATE
    lang = resolve_lang(cv, lang)
    first, _ = _inspect(cv, gaps, tpl, fmt, lang, render_text(cv, lang, tpl))
    if first.ok or not try_alternatives:
        return first
    tried = [{"template": tpl, "ok": False, "score": first.artifact_score}]
    best = first
    for alt in [DEFAULT_TEMPLATE, *TEMPLATES]:
        if alt == tpl or any(t["template"] == alt for t in tried):
            continue
        rep, _ = _inspect(cv, gaps, alt, fmt, lang, render_text(cv, lang, alt))
        tried.append({"template": alt, "ok": rep.ok, "score": rep.artifact_score})
        if rep.ok or rep.artifact_score > best.artifact_score:
            rep.template = tpl
            rep.forced_safe_template = True
            rep.issues = [*first.issues, f"se fuerza la plantilla '{alt}' porque conserva el texto íntegro"] if rep.ok else rep.issues
            best = rep
            if rep.ok:
                break
    best.tried = tried
    return best
