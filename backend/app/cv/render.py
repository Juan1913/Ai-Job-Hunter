"""CV en texto plano de una sola columna con encabezados estándar (ES/EN) y DOCX con estilos reales.

Una única fuente de verdad (`build_doc`) alimenta el texto que lee el ATS, el DOCX y el PDF (pdf.py)."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from app.cv.lang import detect_lang, headings
from app.cv.templates import DEFAULT_TEMPLATE, Template, get_template
from app.domain.tailoring import TailoredCV
from app.profile import dates
from app.text import norm

_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF⭐✅❌✨❤️‍]")
_BAD_BULLET = re.compile(r"^[\s➢➤►▶❖✓✔☑■□◆◇○◦▪▫★☆➔➜→⇒▸‣⁃●♦•*·]+\s*")

def clean(s: object) -> str:
    """Quita emojis/símbolos y espacios raros: lo que ensucia la lectura de un ATS."""
    t = _EMOJI.sub("", str(s or ""))
    t = t.replace("\xa0", " ").replace("\t", " ").replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"[ ]{2,}", " ", t).strip()

def clean_bullet(s: object) -> str:
    return _BAD_BULLET.sub("", clean(s)).strip()

@dataclass
class Entry:
    head: str
    dates: str = ""
    bullets: list[str] = field(default_factory=list)
    org: str = ""                                          # layout Harvard: organización (negrita) ...
    role: str = ""                                         # ... y cargo/título (cursiva) en la línea siguiente

@dataclass
class Section:
    key: str
    heading: str
    paragraph: str = ""
    items: list[str] = field(default_factory=list)         # viñetas simples (certificaciones)
    entries: list[Entry] = field(default_factory=list)
    inline: str = ""                                       # lista en una línea (habilidades, idiomas)

@dataclass
class CVDoc:
    lang: str
    name: str
    contact: str
    headline: str
    sections: list[Section]

def resolve_lang(cv: TailoredCV, lang: str | None = None) -> str:
    if lang in ("es", "en"):
        return lang
    if cv.lang in ("es", "en"):
        return cv.lang
    sample = " ".join([cv.summary, *[b for e in cv.experience for b in e.get("bullets", [])][:6]])
    return detect_lang(sample or cv.headline, "es")

def _sorted_experience(exp: list[dict]) -> list[dict]:
    """Más reciente primero cuando todas las entradas traen fecha de inicio."""
    keys = []
    for e in exp:
        s = dates.parse_date(e.get("start"))
        if not s:
            return exp
        end = dates.parse_date(e.get("end")) if e.get("end") else None
        keys.append(("9999-12" if not end else end + ("-06" if len(end) == 4 else ""), s + ("-06" if len(s) == 4 else "")))
    order = sorted(range(len(exp)), key=lambda i: keys[i], reverse=True)
    return [exp[i] for i in order]

def build_doc(cv: TailoredCV, lang: str | None = None, template: str | None = None) -> CVDoc:
    lang = resolve_lang(cv, lang)
    tpl = get_template(template)
    harvard = tpl.layout == "harvard"
    sep = "-"                      # guion simple: es lo que mejor leen los ATS
    h = headings(lang)
    c = cv.contact or {}
    secs: list[Section] = []
    if clean(cv.summary) and tpl.show_summary:
        secs.append(Section("summary", h["summary"], paragraph=clean(cv.summary)))
    skills, seen = [], set()
    for s in (clean(x) for x in cv.skills):
        if s and norm(s) not in seen:                      # el redactor puede repetir habilidades: una sola vez
            seen.add(norm(s))
            skills.append(s)
    if skills:
        secs.append(Section("skills", h["skills"], inline=", ".join(skills)))
    entries = []
    for e in _sorted_experience(cv.experience):
        title, company = clean(e.get("title")), clean(e.get("company"))
        head = " - ".join(x for x in ((company, title) if harvard else (title, company)) if x)
        start, end = e.get("start"), e.get("end")
        if start or end:
            d = f"{dates.format_date(start, lang)} {sep} {dates.format_date(end, lang) if end else h['present']}" if start else dates.format_date(end, lang)
        else:
            d = ""
        entries.append(Entry(head, d, [b for b in (clean_bullet(x) for x in e.get("bullets", [])) if b], company, title))
    if entries:
        secs.append(Section("experience", h["experience"], entries=entries))
    edu = []
    for e in cv.education:
        deg, inst = clean(e.get("degree")), clean(e.get("institution"))
        head = " - ".join(x for x in ((inst, deg) if harvard else (deg, inst)) if x)
        if head:
            edu.append(Entry(head, clean(e.get("year")), org=inst, role=deg))
    if edu:
        secs.append(Section("education", h["education"], entries=edu))
    if cv.certifications:
        secs.append(Section("certifications", h["certifications"], items=[clean_bullet(x) for x in cv.certifications if clean(x)]))
    if cv.languages:
        secs.append(Section("languages", h["languages"], inline=", ".join(clean(x) for x in cv.languages if clean(x))))
    secs = [s for k in tpl.order for s in secs if s.key == k] + [s for s in secs if s.key not in tpl.order]
    contact = " | ".join(clean(x) for x in (c.get("email"), c.get("phone"), c.get("location")) if x)
    return CVDoc(lang, clean(c.get("full_name")), contact, clean(cv.headline), secs)

def _entry_line(e: Entry) -> str:
    return e.head + (f" ({e.dates})" if e.dates else "")

def render_text(cv: TailoredCV, lang: str | None = None, template: str | None = None) -> str:
    """Texto lógico idéntico, línea a línea, a la capa de texto del PDF/DOCX de la plantilla (en Harvard: organización + fechas, y debajo el cargo)."""
    d = build_doc(cv, lang, template)
    harvard = get_template(template).layout == "harvard"
    out = [d.name.upper(), d.contact, ""]
    if d.headline:
        out += [d.headline, ""]
    for s in d.sections:
        out.append(s.heading)
        if s.paragraph:
            out.append(s.paragraph)
        if s.inline:
            out.append(s.inline)
        for e in s.entries:
            if harvard and e.org:
                out.append(f"{e.org}  {e.dates}".strip())
                if e.role:
                    out.append(e.role)
            else:
                out.append(_entry_line(e))
            out += [f"- {b}" for b in e.bullets]
            if s.key == "experience":
                out.append("")
        out += [f"- {x}" for x in s.items]
        out.append("")
    return "\n".join(out).strip() + "\n"

# --------------------------------------------------------------------------- DOCX

def _rgb(hexs: str):
    from docx.shared import RGBColor
    return RGBColor(int(hexs[0:2], 16), int(hexs[2:4], 16), int(hexs[4:6], 16))

def _set_font(style, font: str, size: float, bold: bool | None = None, color: str | None = None) -> None:
    from docx.oxml.ns import qn
    from docx.shared import Pt
    style.font.name, style.font.size = font, Pt(size)
    if bold is not None:
        style.font.bold = bold
    if color:
        style.font.color.rgb = _rgb(color)
    rpr = style.element.get_or_add_rPr()
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        from docx.oxml import OxmlElement
        rf = OxmlElement("w:rFonts")
        rpr.append(rf)
    for a in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rf.set(qn(a), font)
    for a in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):   # el tema pisaría la fuente elegida
        if rf.get(qn(a)) is not None:
            del rf.attrib[qn(a)]

def _bottom_rule(paragraph, color: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    ppr = paragraph._p.get_or_add_pPr()
    b = OxmlElement("w:pBdr")
    bt = OxmlElement("w:bottom")
    for k, v in (("w:val", "single"), ("w:sz", "4"), ("w:space", "1"), ("w:color", color)):
        bt.set(qn(k), v)
    b.append(bt)
    ppr.append(b)

def render_docx(cv: TailoredCV, template: str | None = DEFAULT_TEMPLATE, lang: str | None = None) -> bytes:
    """DOCX de una columna con estilos reales (Title, Heading 1, List Bullet) y sin tablas ni imágenes."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
    from docx.shared import Cm, Pt

    tpl: Template = get_template(template)
    d = build_doc(cv, lang, tpl.name)
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(tpl.margin_cm)
    sec.top_margin = sec.bottom_margin = Cm(max(1.4, tpl.margin_cm - 0.2))
    st = doc.styles
    _set_font(st["Normal"], tpl.docx_font, tpl.body_pt)
    st["Normal"].paragraph_format.space_after = Pt(2)
    st["Normal"].paragraph_format.line_spacing = tpl.leading
    _set_font(st["Title"], tpl.docx_font, tpl.name_pt, True, tpl.accent)
    st["Title"].paragraph_format.space_after = Pt(2)
    ppr = st["Title"].element.get_or_add_pPr()
    el = ppr.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pBdr")   # Title por defecto trae una línea inferior
    if el is not None:
        ppr.remove(el)
    _set_font(st["Heading 1"], tpl.docx_font, tpl.heading_pt, True, tpl.accent)
    h1 = st["Heading 1"].paragraph_format
    h1.space_before, h1.space_after, h1.keep_with_next = Pt(tpl.section_gap_pt), Pt(3), True
    _set_font(st["Heading 2"], tpl.docx_font, tpl.body_pt + 0.5, True)
    st["Heading 2"].paragraph_format.space_before = Pt(5)
    st["Heading 2"].paragraph_format.space_after = Pt(0)
    st["Heading 2"].paragraph_format.keep_with_next = True
    _set_font(st["List Bullet"], tpl.docx_font, tpl.body_pt)
    lb = st["List Bullet"].paragraph_format
    lb.space_after, lb.left_indent, lb.first_line_indent = Pt(1), Cm(0.6), Cm(-0.4)

    align = WD_ALIGN_PARAGRAPH.CENTER if tpl.name_align == "center" else WD_ALIGN_PARAGRAPH.LEFT
    p = doc.add_paragraph(d.name.upper(), style="Title")
    p.alignment = align
    if d.contact:
        p = doc.add_paragraph(d.contact)
        p.alignment = align
    if d.headline:
        p = doc.add_paragraph()
        r = p.add_run(d.headline)
        r.bold = True
        p.alignment = align
    for s in d.sections:
        hp = doc.add_paragraph(s.heading, style="Heading 1")
        if tpl.rule:
            _bottom_rule(hp, tpl.accent if tpl.accent != "000000" else "808080")
        if s.paragraph:
            doc.add_paragraph(s.paragraph)
        if s.inline:
            doc.add_paragraph(s.inline)
        for e in s.entries:
            ep = doc.add_paragraph(style="Heading 2")
            if tpl.layout == "harvard" and e.org:
                ep.paragraph_format.tab_stops.add_tab_stop(Cm(21.0 - 2 * tpl.margin_cm), WD_TAB_ALIGNMENT.RIGHT)
                ep.add_run(e.org)
                if e.dates:
                    ep.add_run("\t" + e.dates).bold = False
                if e.role:
                    rp = doc.add_paragraph()
                    rp.paragraph_format.keep_with_next = bool(e.bullets)
                    rp.add_run(e.role).italic = True
            else:
                ep.add_run(e.head)
                if e.dates:
                    dr = ep.add_run(f" ({e.dates})")
                    dr.bold = False
            for b in e.bullets:
                doc.add_paragraph(b, style="List Bullet")
        for x in s.items:
            doc.add_paragraph(x, style="List Bullet")
    cp = doc.core_properties
    cp.title = f"CV - {d.name}"
    cp.author = d.name
    cp.subject = d.headline
    cp.language = "es-ES" if d.lang == "es" else "en-US"
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
