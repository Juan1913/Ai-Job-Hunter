"""Ajuste a páginas con recorte por relevancia.

Objetivo configurable (CV: 1 o 2 páginas; carta: 1). Las páginas se MIDEN con reportlab (el mismo motor que genera el PDF), no se
adivinan: se renderiza y se cuentan las páginas. Si sobra contenido se recorta viñeta a viñeta, siempre la de menor valor:

    valor = 0.40 relevancia a la oferta (palabras clave respaldadas que cubre)
          + 0.20 cifras (logros medibles)
          + 0.20 recencia (la experiencia más reciente pesa más)
          + 0.15 unicidad (no repetir lo que ya dice otra viñeta)
          + 0.05 contribución única a palabras clave

Reglas duras: nunca se elimina una experiencia entera ni se deja la más reciente con menos de 2 viñetas (el resto conserva al menos 1);
nunca se pierde una palabra clave obligatoria que solo cubra esa viñeta (se verifica re-renderizando el texto). Todo recorte se
reporta con su motivo. El recorte solo QUITA texto: no puede introducir nada nuevo.

La medición usa las fuentes estándar del PDF (Helvetica/Times) que tienen las mismas métricas que Arial/Times New Roman del DOCX;
en la plantilla 'modern' el DOCX usa Calibri (más estrecha), así que la estimación es conservadora (el DOCX no ocupará más páginas)."""
from __future__ import annotations

import io
import re

from pydantic import BaseModel, Field

from app.cv.render import _sorted_experience, render_text
from app.cv.templates import DEFAULT_TEMPLATE, TEMPLATES, get_template
from app.domain.tailoring import KeywordGap, TailoredCV, includable
from app.text import covered, norm

MIN_BULLETS_RECENT, MIN_BULLETS_OLD = 2, 1
W_KW, W_NUM, W_REC, W_UNI, W_UNIQKW = 0.40, 0.20, 0.20, 0.15, 0.05


class Trim(BaseModel):
    kind: str                       # bullet | sentence
    company: str = ""
    title: str = ""
    text: str
    score: float
    reason: str


class FitResult(BaseModel):
    target_pages: int
    template: str
    pages_before: int
    pages_after: int
    fits: bool
    trimmed: list[Trim] = Field(default_factory=list)
    protected: list[str] = Field(default_factory=list)      # viñetas que no se tocaron y por qué (resumen)
    pages_by_template: dict[str, int] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    def compact(self) -> dict:
        return self.model_dump(mode="json")


# --------------------------------------------------------------------------- medición

def count_pages(pdf: bytes) -> int:
    from pypdf import PdfReader
    return len(PdfReader(io.BytesIO(pdf)).pages)


def measure_pages(cv: TailoredCV, template: str | None = DEFAULT_TEMPLATE, lang: str | None = None) -> int:
    """Páginas reales del PDF que se entregaría con esa plantilla (se renderiza y se cuenta)."""
    from app.cv.pdf import render_pdf
    return count_pages(render_pdf(cv, template, lang))


def measure_letter_pages(text: str, template: str | None = DEFAULT_TEMPLATE) -> int:
    from xml.sax.saxutils import escape

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    from app.cv.pdf import pdf_safe
    tpl = get_template(template)
    st = ParagraphStyle("letter", fontName=tpl.pdf_font, fontSize=tpl.body_pt + 0.5, leading=(tpl.body_pt + 0.5) * 1.35, spaceAfter=8)
    story: list = []
    for p in re.split(r"\n\s*\n", text.strip()):
        story.append(Paragraph(escape(pdf_safe(p)).replace("\n", "<br/>"), st))
    story.append(Spacer(1, 1))
    buf = io.BytesIO()
    m = max(tpl.margin_cm, 2.0) * cm
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=m, rightMargin=m, topMargin=2.0 * cm, bottomMargin=2.0 * cm).build(story)
    return count_pages(buf.getvalue())


# --------------------------------------------------------------------------- puntuación de viñetas

def _stems(s: str) -> set[str]:
    return {t[:6] for t in re.findall(r"[a-z0-9+#&]{3,}", norm(s))}


def _numbers(s: str) -> int:
    return len(re.findall(r"\d+(?:[.,]\d+)?\s?%?", s))


def _targets(gaps: list[KeywordGap] | None) -> list[str]:
    return [g.keyword for g in (gaps or []) if includable(g)]


def _covered_set(cv: TailoredCV, targets: list[str]) -> set[str]:
    t = norm(render_text(cv))
    return {k for k in targets if covered(t, k)}


def _recency_rank(cv: TailoredCV) -> dict[int, int]:
    """índice en cv.experience -> posición (0 = la más reciente)."""
    ordered = _sorted_experience(cv.experience)
    pos = {id(e): i for i, e in enumerate(ordered)}
    return {i: pos[id(e)] for i, e in enumerate(cv.experience)}


def score_bullets(cv: TailoredCV, gaps: list[KeywordGap] | None) -> list[dict]:
    """Una entrada por viñeta: {exp, idx, text, score, parts, reason}. Mayor puntaje = más valiosa."""
    targets = _targets(gaps)
    rank = _recency_rank(cv)
    all_b = [(ei, bi, b) for ei, e in enumerate(cv.experience) for bi, b in enumerate(e.get("bullets", []) or []) if isinstance(b, str) and b.strip()]
    stems = {(ei, bi): _stems(b) for ei, bi, b in all_b}
    out = []
    for ei, bi, b in all_b:
        bn = norm(b)
        hits = [k for k in targets if covered(bn, k)]
        # palabras clave que SOLO cubre esta viñeta en todo el texto del CV
        rest = norm(render_text(_without(cv, ei, bi)))
        solo = [k for k in hits if not covered(rest, k)]
        kw = min(1.0, len(hits) / 3) if targets else 0.0
        num = min(1.0, _numbers(b) / 2)
        rec = 1.0 / (1 + rank.get(ei, 0))
        sims = [len(stems[(ei, bi)] & s) / max(len(stems[(ei, bi)] | s), 1) for (xe, xb), s in stems.items() if (xe, xb) != (ei, bi)]
        sim = max(sims, default=0.0)
        uni = 1.0 - sim
        uq = 1.0 if solo else 0.0
        score = W_KW * kw + W_NUM * num + W_REC * rec + W_UNI * uni + W_UNIQKW * uq
        why = [f"{len(hits)} palabras clave de la oferta" if hits else "sin palabras clave de la oferta",
               f"{_numbers(b)} cifras" if _numbers(b) else "sin cifras",
               "experiencia más reciente" if rank.get(ei, 0) == 0 else f"experiencia nº{rank.get(ei, 0) + 1} en antigüedad"]
        if sim >= 0.5:
            why.append(f"se parece {sim:.0%} a otra viñeta")
        if solo:
            why.append("única que cubre: " + ", ".join(solo))
        out.append({"exp": ei, "idx": bi, "text": b, "score": round(score, 3), "solo": solo, "reason": "; ".join(why)})
    return out


def _drop(cv: TailoredCV, removed: dict[int, set[int]]) -> TailoredCV:
    c = cv.model_copy(deep=True)
    for ei, idxs in removed.items():
        c.experience[ei]["bullets"] = [b for j, b in enumerate(c.experience[ei].get("bullets", []) or []) if j not in idxs]
    return c


def _without(cv: TailoredCV, ei: int, bi: int) -> TailoredCV:
    return _drop(cv, {ei: {bi}})


# --------------------------------------------------------------------------- recorte

def fit_cv(cv: TailoredCV, target_pages: int = 2, template: str | None = DEFAULT_TEMPLATE, gaps: list[KeywordGap] | None = None,
           lang: str | None = None) -> tuple[TailoredCV, FitResult]:
    """Devuelve (cv recortado, informe). Si ya cabe, devuelve el mismo contenido sin tocar."""
    tpl = template if template in TEMPLATES else DEFAULT_TEMPLATE
    target = 1 if target_pages <= 1 else 2
    before = measure_pages(cv, tpl, lang)
    res = FitResult(target_pages=target, template=tpl, pages_before=before, pages_after=before, fits=before <= target)
    res.pages_by_template = {name: (before if name == tpl else measure_pages(cv, name, lang)) for name in TEMPLATES}
    if res.fits:
        res.notes.append("ya cabe: no se recortó nada")
        return cv, res

    # 1) orden de recorte: de menor a mayor valor, respetando las reglas duras (se simula sin medir páginas)
    targets = _targets(gaps)
    base_cov = _covered_set(cv, targets)
    rank = _recency_rank(cv)
    sim = cv.model_copy(deep=True)
    counts = {ei: len([b for b in e.get("bullets", []) or [] if isinstance(b, str) and b.strip()]) for ei, e in enumerate(sim.experience)}
    sequence: list[dict] = []
    removed: dict[int, set[int]] = {}
    for s in sorted(score_bullets(cv, gaps), key=lambda x: (x["score"], -x["exp"], -x["idx"])):
        floor = MIN_BULLETS_RECENT if rank.get(s["exp"], 1) == 0 else MIN_BULLETS_OLD
        if counts[s["exp"]] <= floor:
            continue
        trial = _drop(sim, {**removed, s["exp"]: removed.get(s["exp"], set()) | {s["idx"]}})
        after_cov = _covered_set(trial, targets)
        if not base_cov <= after_cov:                          # perdería una palabra clave obligatoria
            note = f"protegida: es la única que cubre {', '.join(sorted(base_cov - after_cov))}"
            if note not in res.protected:
                res.protected.append(note)
            continue
        removed.setdefault(s["exp"], set()).add(s["idx"])
        counts[s["exp"]] -= 1
        sequence.append(s)
    # 2) cuántas quitar: el mínimo que cabe (las páginas bajan de forma monótona al quitar texto -> búsqueda binaria sobre páginas MEDIDAS)
    def after(k: int) -> TailoredCV:
        acc: dict[int, set[int]] = {}
        for s in sequence[:k]:
            acc.setdefault(s["exp"], set()).add(s["idx"])
        return _drop(cv, acc)

    lo, hi = 0, len(sequence)
    if measure_pages(after(hi), tpl, lang) > target:
        lo = hi
        res.notes.append("no quedan viñetas que se puedan quitar sin romper las reglas (última experiencia, palabras clave obligatorias)")
    else:
        while lo < hi:
            mid = (lo + hi) // 2
            if measure_pages(after(mid), tpl, lang) <= target:
                hi = mid
            else:
                lo = mid + 1
    work = after(lo)
    for s in sequence[:lo]:
        e = cv.experience[s["exp"]]
        res.trimmed.append(Trim(kind="bullet", company=str(e.get("company") or ""), title=str(e.get("title") or ""), text=s["text"], score=s["score"], reason=s["reason"]))
    work.text = render_text(work, lang)
    res.pages_after = measure_pages(work, tpl, lang)
    res.fits = res.pages_after <= target
    if not res.fits:
        denser = [n for n, p in res.pages_by_template.items() if p <= target]
        res.notes.append(f"con '{tpl}' sigue en {res.pages_after} páginas tras recortar" + (f"; la plantilla {', '.join(denser)} lo contenía sin recortar" if denser else
                                                                                      "; reduce el resumen o las experiencias más antiguas"))
    elif res.trimmed:
        res.notes.append(f"se quitaron {len(res.trimmed)} viñetas de menor valor para llegar a {res.pages_after} página(s)")
    # el contenido restante sigue siendo, palabra por palabra, el del CV original: solo se quitó texto
    return work, res


def fit_letter(text: str, keywords: list[str] | None = None, template: str | None = DEFAULT_TEMPLATE, max_pages: int = 1) -> tuple[str, FitResult]:
    """Carta a 1 página: si sobra, quita las frases de menor valor (palabras clave + cifras), nunca el saludo, la despedida ni la
    primera frase del primer párrafo."""
    tpl = template if template in TEMPLATES else DEFAULT_TEMPLATE
    before = measure_letter_pages(text, tpl)
    res = FitResult(target_pages=max_pages, template=tpl, pages_before=before, pages_after=before, fits=before <= max_pages)
    if res.fits:
        return text, res
    paras = [re.split(r"(?<=[.!?])\s+", p.strip()) for p in re.split(r"\n\s*\n", text.strip())]
    kws = keywords or []
    guard = 0

    def render(ps: list[list[str]]) -> str:
        return "\n\n".join(" ".join(s) for s in ps if s)

    while measure_letter_pages(render(paras), tpl) > max_pages and guard < 100:
        guard += 1
        cands = []
        for pi, ss in enumerate(paras):
            if pi in (0, len(paras) - 1) and len(paras) > 2:      # saludo / cierre
                continue
            for si, s in enumerate(ss):
                if pi == 1 and si == 0 or len(ss) <= 1:
                    continue
                val = sum(1 for k in kws if norm(k) in norm(s)) * 2 + _numbers(s) + min(len(s), 200) / 400
                cands.append((val, pi, si, s))
        if not cands:
            res.notes.append("no quedan frases que se puedan quitar sin tocar saludo/cierre")
            break
        val, pi, si, s = min(cands)
        res.trimmed.append(Trim(kind="sentence", text=s, score=round(val, 2), reason="frase de menor valor (pocas palabras clave y sin cifras)"))
        paras[pi].pop(si)
    out = render(paras)
    res.pages_after = measure_letter_pages(out, tpl)
    res.fits = res.pages_after <= max_pages
    return out, res
