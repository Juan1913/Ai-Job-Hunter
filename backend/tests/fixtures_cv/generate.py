"""Genera los archivos de CV de prueba: `python -m tests.fixtures_cv.generate` (desde backend/).

PDFs de 1 y 2 columnas (las de 2 columnas se dibujan INTERCALADAS por altura, como hacen muchos
exportadores: es el caso que rompe a los extractores ingenuos), DOCX con tablas y cuadros de texto
y un PDF escaneado (solo imagen, sin texto)."""
import io
from pathlib import Path

from tests.fixtures_cv.data import CVS, VARIANTS

OUT = Path(__file__).parent
H = {"es": {"summary": "Perfil", "skills": "Habilidades", "exp": "Experiencia laboral", "edu": "Educación",
            "lang": "Idiomas", "cert": "Certificaciones", "contact": "Contacto", "present": "Actualidad"},
     "en": {"summary": "Profile", "skills": "Skills", "exp": "Work Experience", "edu": "Education",
            "lang": "Languages", "cert": "Certifications", "contact": "Contact", "present": "Present"}}

def _lines(cv: dict) -> list[str]:
    h = H[cv["lang"]]
    out = [cv["name"], cv["headline"], " | ".join(cv["contact"]), "", h["summary"].upper(), cv["summary"], "", h["skills"].upper(), ", ".join(cv["skills"]), "", h["exp"].upper()]
    for co, ti, s, e, bl in cv["experience"]:
        out += [f"{ti} - {co}", f"{s} - {e}", *[f"- {b}" for b in bl], ""]
    out += [h["edu"].upper(), *[f"{d} - {i} ({y})" for i, d, y in cv["education"]], "", h["lang"].upper(), ", ".join(cv["languages"]), "", h["cert"].upper(), *cv["certs"]]
    return out

def to_txt(cv: dict) -> bytes:
    return "\n".join(_lines(cv)).encode()

def _wrap(text: str, font: str, size: float, width: float) -> list[str]:
    from reportlab.lib.utils import simpleSplit
    return simpleSplit(text, font, size, width)

def to_pdf_1col(cv: dict) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, hgt = A4
    y = hgt - 50
    def put(text, font="Helvetica", size=10, gap=14, bullet=False):
        nonlocal y
        for ln in _wrap(("• " if bullet else "") + text, font, size, w - 100):
            if y < 50:
                c.showPage(); y = hgt - 50
            c.setFont(font, size); c.drawString(50, y, ln); y -= gap
    put(cv["name"], "Helvetica-Bold", 18, 22)
    put(cv["headline"], "Helvetica", 12, 16)
    put("  |  ".join(cv["contact"]), size=9)
    h = H[cv["lang"]]
    y -= 6
    for key, body in (("summary", [cv["summary"]]), ("skills", [", ".join(cv["skills"])])):
        put(h[key].upper(), "Helvetica-Bold", 11, 16); [put(b) for b in body]; y -= 6
    put(h["exp"].upper(), "Helvetica-Bold", 11, 16)
    for co, ti, s, e, bl in cv["experience"]:
        put(f"{ti} - {co}", "Helvetica-Bold", 10); put(f"{s} - {e}", "Helvetica-Oblique", 9)
        for b in bl:
            put(b, bullet=True)
        y -= 4
    put(h["edu"].upper(), "Helvetica-Bold", 11, 16)
    for i, d, yr in cv["education"]:
        put(f"{d} - {i} ({yr})")
    y -= 6
    put(h["lang"].upper(), "Helvetica-Bold", 11, 16); put(", ".join(cv["languages"])); y -= 6
    put(h["cert"].upper(), "Helvetica-Bold", 11, 16)
    for x in cv["certs"]:
        put(x, bullet=True)
    c.save()
    return buf.getvalue()

def to_pdf_2col(cv: dict) -> bytes:
    """Barra lateral izquierda (contacto, habilidades, idiomas) + columna principal; todo intercalado por altura."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    h = H[cv["lang"]]
    w, hgt = A4
    SB, MX, MW = 40, 205, w - 205 - 40
    items: list[tuple[float, float, str, str, float]] = []   # (y, x, texto, fuente, tamaño)
    def col(x, width, y0, blocks):
        y = y0
        for text, font, size, gap, bullet in blocks:
            for ln in _wrap(("• " if bullet else "") + text, font, size, width):
                items.append((y, x, ln, font, size)); y -= gap
        return y
    side = [("CONTACTO" if cv["lang"] == "es" else "CONTACT", "Helvetica-Bold", 11, 16, False)] + [(x, "Helvetica", 9, 12, False) for x in cv["contact"]]
    side += [("", "Helvetica", 9, 10, False), (h["skills"].upper(), "Helvetica-Bold", 11, 16, False)] + [(s, "Helvetica", 9, 12, False) for s in cv["skills"]]
    side += [("", "Helvetica", 9, 10, False), (h["lang"].upper(), "Helvetica-Bold", 11, 16, False)] + [(x, "Helvetica", 9, 12, False) for x in cv["languages"]]
    main = [(cv["name"], "Helvetica-Bold", 20, 24, False), (cv["headline"], "Helvetica", 12, 18, False),
            (h["summary"].upper(), "Helvetica-Bold", 11, 16, False), (cv["summary"], "Helvetica", 9.5, 13, False), ("", "Helvetica", 9, 8, False),
            (h["exp"].upper(), "Helvetica-Bold", 11, 16, False)]
    for co, ti, s, e, bl in cv["experience"]:
        main += [(f"{ti} - {co}", "Helvetica-Bold", 10, 13, False), (f"{s} - {e}", "Helvetica-Oblique", 9, 13, False)]
        main += [(b, "Helvetica", 9.5, 12.5, True) for b in bl] + [("", "Helvetica", 9, 6, False)]
    main += [(h["edu"].upper(), "Helvetica-Bold", 11, 16, False)] + [(f"{d} - {i} ({y})", "Helvetica", 9.5, 13, False) for i, d, y in cv["education"]]
    main += [("", "Helvetica", 9, 8, False), (h["cert"].upper(), "Helvetica-Bold", 11, 16, False)] + [(x, "Helvetica", 9.5, 13, True) for x in cv["certs"]]
    col(SB, 150, hgt - 60, side)
    col(MX, MW, hgt - 50, main)
    items.sort(key=lambda t: (-round(t[0] / 3), t[1]))      # orden de dibujo: fila a fila, mezclando columnas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFillGray(0.93); c.rect(0, 0, 190, hgt, stroke=0, fill=1); c.setFillGray(0)
    for y, x, t, f, s in items:
        c.setFont(f, s); c.drawString(x, y, t)
    c.save()
    return buf.getvalue()

def to_pdf_scan(cv: dict) -> bytes:
    """PDF de una imagen (como un escaneo): sin capa de texto."""
    from PIL import Image, ImageDraw, ImageFont
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    img = Image.new("L", (1240, 1754), 255)
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=22)
    y = 60
    for ln in _lines(cv):
        for part in _wrap(ln, "Helvetica", 11, 80) or [""]:
            d.text((70, y), part, fill=0, font=font); y += 30
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawImage(ImageReader(img), 0, 0, *A4)
    c.save()
    return buf.getvalue()

def _docx_blocks(doc, cv: dict, where, sections: list[str]):
    """Escribe en `where` (documento o celda) las secciones indicadas."""
    h = H[cv["lang"]]
    def head(t):
        p = where.add_paragraph(); r = p.add_run(t.upper()); r.bold = True
    if "name" in sections:
        p = where.add_paragraph(); r = p.add_run(cv["name"]); r.bold = True
        where.add_paragraph(cv["headline"])
    if "contact" in sections:
        head(h["contact"]); [where.add_paragraph(x) for x in cv["contact"]]
    if "summary" in sections:
        head(h["summary"]); where.add_paragraph(cv["summary"])
    if "skills" in sections:
        head(h["skills"]); [where.add_paragraph(s, style="List Bullet") for s in cv["skills"]]
    if "exp" in sections:
        head(h["exp"])
        for co, ti, s, e, bl in cv["experience"]:
            p = where.add_paragraph(); p.add_run(f"{ti} - {co}").bold = True
            where.add_paragraph(f"{s} - {e}")
            [where.add_paragraph(b, style="List Bullet") for b in bl]
    if "edu" in sections:
        head(h["edu"]); [where.add_paragraph(f"{d} - {i} ({y})") for i, d, y in cv["education"]]
    if "lang" in sections:
        head(h["lang"]); where.add_paragraph(", ".join(cv["languages"]))
    if "cert" in sections:
        head(h["cert"]); [where.add_paragraph(x) for x in cv["certs"]]

def to_docx(cv: dict, layout: str) -> bytes:
    from xml.sax.saxutils import escape

    from docx import Document
    from docx.oxml import parse_xml
    doc = Document()
    if layout == "1col":
        _docx_blocks(doc, cv, doc, ["name", "contact", "summary", "skills", "exp", "edu", "lang", "cert"])
    elif layout == "table":
        # Plantilla típica: tabla de 2 celdas sin bordes (barra lateral + principal)
        t = doc.add_table(rows=1, cols=2)
        left, right = t.rows[0].cells
        _docx_blocks(doc, cv, left, ["contact", "skills", "lang"])
        _docx_blocks(doc, cv, right, ["name", "summary", "exp", "edu", "cert"])
    elif layout == "textbox":
        # Contacto en el ENCABEZADO de página y la barra lateral dentro de un cuadro de texto
        hp = doc.sections[0].header.paragraphs[0]
        hp.text = " | ".join(cv["contact"])
        _docx_blocks(doc, cv, doc, ["name", "summary", "exp", "edu", "cert"])
        h = H[cv["lang"]]
        paras = "".join(f"<w:p><w:r><w:t>{escape(x)}</w:t></w:r></w:p>" for x in [h["skills"].upper(), *cv["skills"], h["lang"].upper(), *cv["languages"]])
        xml = ('<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:v="urn:schemas-microsoft-com:vml">'
               f'<w:pict><v:shape style="width:150pt;height:300pt"><v:textbox><w:txbxContent>{paras}</w:txbxContent></v:textbox></v:shape></w:pict></w:r>')
        doc.add_paragraph()._p.append(parse_xml(xml))
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()

def build(cv_key: str, fmt: str, layout: str) -> bytes:
    cv = CVS[cv_key]
    if fmt == "txt":
        return to_txt(cv)
    if fmt == "docx":
        return to_docx(cv, layout)
    return {"1col": to_pdf_1col, "2col": to_pdf_2col, "scan": to_pdf_scan}[layout](cv)

def main() -> None:
    for name, key, fmt, layout in VARIANTS:
        (OUT / f"{name}.{fmt}").write_bytes(build(key, fmt, layout))
        print("generado", f"{name}.{fmt}")

if __name__ == "__main__":
    main()
