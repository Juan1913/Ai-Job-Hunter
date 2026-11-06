"""PDF ATS-friendly: una columna, fuentes estándar (no incrustadas), texto seleccionable, sin tablas ni imágenes.

Usa reportlab/platypus: el orden de dibujo es el orden de lectura, y cada párrafo es texto real."""
from __future__ import annotations

import io
import unicodedata
from xml.sax.saxutils import escape

from app.cv.render import build_doc
from app.cv.templates import DEFAULT_TEMPLATE, get_template
from app.domain.tailoring import TailoredCV

_TRANSLIT = {"Ł": "L", "ł": "l", "Đ": "D", "đ": "d", "Ø": "O", "ø": "o", "ı": "i", "İ": "I", "ß": "ss"}

def pdf_safe(s: str) -> str:
    """Las fuentes estándar de PDF cubren WinAnsi (cp1252): lo demás se translitera o se descarta, sin cuadritos."""
    out = []
    for ch in s:
        try:
            ch.encode("cp1252")
            out.append(ch)
        except UnicodeEncodeError:
            base = _TRANSLIT.get(ch) or unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
            out.append(base)
    return "".join(out)

def render_pdf(cv: TailoredCV, template: str | None = DEFAULT_TEMPLATE, lang: str | None = None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import Flowable, HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer

    tpl = get_template(template)
    d = build_doc(cv, lang, tpl.name)
    accent = colors.HexColor("#" + tpl.accent)
    body = ParagraphStyle("body", fontName=tpl.pdf_font, fontSize=tpl.body_pt, leading=tpl.body_pt * tpl.leading, spaceAfter=2)
    align = TA_CENTER if tpl.name_align == "center" else TA_LEFT
    name_st = ParagraphStyle("name", parent=body, fontName=tpl.pdf_bold, fontSize=tpl.name_pt, leading=tpl.name_pt * 1.15, alignment=align, textColor=accent)
    ctr = ParagraphStyle("ctr", parent=body, alignment=align)
    head_line = ParagraphStyle("hl", parent=body, fontName=tpl.pdf_bold, alignment=align, spaceAfter=3)
    sec_st = ParagraphStyle("sec", parent=body, fontName=tpl.pdf_bold, fontSize=tpl.heading_pt, leading=tpl.heading_pt * 1.2, textColor=accent,
                            spaceBefore=tpl.section_gap_pt, spaceAfter=2, keepWithNext=1)
    ent_st = ParagraphStyle("ent", parent=body, fontName=tpl.pdf_bold, spaceBefore=4, spaceAfter=1, keepWithNext=1)
    bul_st = ParagraphStyle("bul", parent=body, leftIndent=14, bulletIndent=3, bulletFontName=tpl.pdf_font, spaceAfter=1)

    class LR(Flowable):
        """Una línea con texto en negrita a la izquierda y fechas pegadas al margen derecho (Harvard). Es texto real: sin tablas."""
        def __init__(self, left: str, right: str):
            super().__init__()
            self.left, self.right, self.size = pdf_safe(left), pdf_safe(right), tpl.body_pt
            self.lines: list[str] = []

        def wrap(self, aw, ah):
            self.aw = aw
            room = aw - (stringWidth(self.right, tpl.pdf_font, self.size) + 8 if self.right else 0)
            words, cur, self.lines = self.left.split(), "", []
            for w in words:
                t = f"{cur} {w}".strip()
                if cur and stringWidth(t, tpl.pdf_bold, self.size) > room:
                    self.lines.append(cur)
                    cur = w
                else:
                    cur = t
            self.lines.append(cur)
            self.height = len(self.lines) * self.size * tpl.leading + 3
            return aw, self.height

        def draw(self):
            c, y = self.canv, self.height - self.size - 1
            for i, ln in enumerate(self.lines):
                c.setFont(tpl.pdf_bold, self.size)
                c.drawString(0, y, ln)
                if i == 0 and self.right:
                    c.setFont(tpl.pdf_font, self.size)
                    c.drawRightString(self.aw, y, self.right)
                y -= self.size * tpl.leading

    def P(text: str, style, **kw) -> Paragraph:
        return Paragraph(escape(pdf_safe(text)), style, **kw)

    story: list = [P(d.name.upper(), name_st)]
    if d.contact:
        story.append(P(d.contact, ctr))
    if d.headline:
        story.append(P(d.headline, head_line))
    for s in d.sections:
        block = [P(s.heading, sec_st)]
        if tpl.rule:
            block.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#808080") if tpl.accent == "000000" else accent, spaceAfter=3))
        story.append(KeepTogether(block))
        if s.paragraph:
            story.append(P(s.paragraph, body))
        if s.inline:
            story.append(P(s.inline, body))
        for e in s.entries:
            if tpl.layout == "harvard" and e.org:
                ital = ParagraphStyle("it", parent=body, fontName=tpl.pdf_italic, spaceAfter=1, keepWithNext=1 if e.bullets else 0)
                bl = [P(b, bul_st, bulletText="•") for b in e.bullets]
                story.append(KeepTogether([Spacer(1, 3), LR(e.org, e.dates), *([P(e.role, ital)] if e.role else []), *bl[:1]]))
                story.extend(bl[1:])
                continue
            head = f"<b>{escape(pdf_safe(e.head))}</b>" + (f" ({escape(pdf_safe(e.dates))})" if e.dates else "")
            first = [Paragraph(head, ParagraphStyle("e2", parent=ent_st, fontName=tpl.pdf_font, keepWithNext=1 if e.bullets else 0))]
            bl = [P(b, bul_st, bulletText="•") for b in e.bullets]
            story.append(KeepTogether(first + bl[:1]))
            story.extend(bl[1:])
        for x in s.items:
            story.append(P(x, bul_st, bulletText="•"))
    story.append(Spacer(1, 1))
    buf = io.BytesIO()
    m = tpl.margin_cm * cm
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=m, rightMargin=m, topMargin=max(1.4, tpl.margin_cm - 0.2) * cm,
                            bottomMargin=max(1.4, tpl.margin_cm - 0.2) * cm, title=pdf_safe(f"CV - {d.name}"), author=pdf_safe(d.name),
                            subject=pdf_safe(d.headline), creator="JobHunter", pageCompression=1)
    doc.build(story)
    return buf.getvalue()
