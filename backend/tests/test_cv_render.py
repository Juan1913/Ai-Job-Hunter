import io
import re

import pytest
from docx import Document

from app.ats.checker import check
from app.cv.pdf import pdf_safe, render_pdf
from app.cv.render import build_doc, render_docx, render_text, resolve_lang
from app.cv.templates import TEMPLATES
from app.domain.tailoring import GapStatus, KeywordGap, TailoredCV
from app.profile.extract import extract_document


def make_cv(**kw) -> TailoredCV:
    base = dict(
        headline="Analista SOC", summary="Analista de seguridad con 4 años de experiencia en monitoreo SIEM y respuesta a incidentes.",
        skills=["SIEM", "Splunk", "MITRE ATT&CK", "Python"],
        experience=[
            {"company": "Acme", "title": "Analista de Seguridad", "start": "2021-03", "end": None,
             "bullets": ["Monitoreé alertas SIEM en Splunk las 24 horas", "Respondí a incidentes de phishing y malware"]},
            {"company": "Globex", "title": "Soporte TI", "start": "2018-01", "end": "2021-02", "bullets": ["Atendí 40 tickets semanales"]}],
        education=[{"institution": "UNAL", "degree": "Ingeniería de Sistemas", "year": "2018"}], certifications=["CEH", "Security+"],
        languages=["Español (nativo)", "Inglés (B2)"],
        contact={"full_name": "Luis Gómez", "email": "luis@x.com", "phone": "+57 300 123 4567", "location": "Bogotá"})
    base.update(kw)
    return TailoredCV(**base)

def test_text_has_standard_spanish_sections_and_formatted_dates():
    t = render_text(make_cv())
    for h in ("RESUMEN PROFESIONAL", "HABILIDADES", "EXPERIENCIA LABORAL", "EDUCACIÓN", "CERTIFICACIONES", "IDIOMAS"):
        assert h in t
    assert "Acme  Mar 2021 - Actualidad\nAnalista de Seguridad" in t and "Globex  Ene 2018 - Feb 2021\nSoporte TI" in t
    assert t.index("EDUCACIÓN") < t.index("EXPERIENCIA LABORAL")                       # Harvard: la educación va primero
    c = render_text(make_cv(), template="classic")
    assert "Analista de Seguridad - Acme (Mar 2021 - Actualidad)" in c and c.index("EXPERIENCIA LABORAL") < c.index("EDUCACIÓN")
    assert t.startswith("LUIS GÓMEZ\nluis@x.com | +57 300 123 4567 | Bogotá")

def test_english_cv_uses_english_headings_dates_and_present():
    cv = make_cv(summary="Security analyst with 4 years of experience in SIEM monitoring and incident response.",
                 experience=[{"company": "Acme", "title": "Security Analyst", "start": "2021-03", "end": None, "bullets": ["Monitored alerts in Splunk"]}])
    assert resolve_lang(cv) == "en"
    t = render_text(cv)
    for h in ("PROFESSIONAL SUMMARY", "SKILLS", "WORK EXPERIENCE", "EDUCATION", "CERTIFICATIONS", "LANGUAGES"):
        assert h in t
    assert "Mar 2021 - Present" in t
    assert "EXPERIENCIA" in render_text(cv, "es")                       # idioma forzado (el de la oferta) manda sobre el contenido

def test_experience_is_sorted_reverse_chronologically_and_no_fake_present():
    cv = make_cv(experience=[{"company": "Viejo", "title": "A", "start": "2010", "end": "2012", "bullets": []},
                             {"company": "Nuevo", "title": "B", "start": "2020", "end": "2023", "bullets": []},
                             {"company": "Actual", "title": "C", "start": "2023-06", "end": None, "bullets": []}])
    t = render_text(cv)
    assert t.index("Actual") < t.index("Nuevo") < t.index("Viejo")
    nodates = render_text(make_cv(experience=[{"company": "X", "title": "Y", "start": None, "end": None, "bullets": ["b"]}]))
    assert "Actualidad" not in nodates.split("EXPERIENCIA LABORAL")[1].split("EDUCACI")[0]      # sin fechas no se inventa 'Actualidad'

def test_problematic_characters_are_stripped():
    cv = make_cv(summary="Líder 🚀 apasionado ✨", experience=[{"company": "Acme", "title": "Dev", "start": "2020", "end": None, "bullets": ["➢ Hice cosas ✓", "★ Otra cosa 😀"]}])
    t = render_text(cv)
    assert not any(c in t for c in "🚀✨➢✓★😀")
    assert "- Hice cosas" in t and next(r for r in check(t, []).breakdown if r.rule == "characters").ok

def test_pdf_safe_transliterates_unsupported_glyphs():
    assert pdf_safe("Gómez – “ok” • ñ") == "Gómez – “ok” • ñ"
    assert pdf_safe("Łódź 日本") == "Lódz "

_ENTRY = {t: "Acme  Mar 2021 - Actualidad" if t == "harvard" else "Analista de Seguridad - Acme (Mar 2021 - Actualidad)" for t in TEMPLATES}
_EDU = {t: "UNAL  2018" if t == "harvard" else "Ingeniería de Sistemas - UNAL (2018)" for t in TEMPLATES}

@pytest.mark.parametrize("template", list(TEMPLATES))
def test_pdf_round_trip_is_ats_safe(template):
    import pdfplumber
    cv = make_cv()
    pdf = render_pdf(cv, template)
    assert pdf.startswith(b"%PDF")
    r = extract_document("cv.pdf", pdf)
    assert not r.columns_detected and not r.scanned
    for needle in ("LUIS GÓMEZ", "luis@x.com", "+57 300 123 4567", "RESUMEN PROFESIONAL", "HABILIDADES", "EXPERIENCIA LABORAL", "EDUCACIÓN",
                   "SIEM, Splunk, MITRE ATT&CK, Python", _ENTRY[template], "• Monitoreé alertas SIEM en Splunk las 24 horas",
                   _EDU[template], "• CEH", "Español (nativo), Inglés (B2)"):
        assert needle in r.text, needle
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        assert doc.metadata.get("Title") == "CV - Luis Gómez" and doc.metadata.get("Author") == "Luis Gómez"
        for page in doc.pages:
            assert not page.images and page.rects.__class__ is not None
            fonts = {c["fontname"].split("+")[-1] for c in page.chars}
            assert fonts <= {"Helvetica", "Helvetica-Bold", "Times-Roman", "Times-Bold", "Times-Italic", "Helvetica-Oblique"}, fonts
    assert check(r.text, []).score >= 74

def test_templates_look_different_but_say_the_same():
    pdfs = {t: render_pdf(make_cv(), t) for t in TEMPLATES}
    assert len({p for p in pdfs.values()}) == len(TEMPLATES)
    texts = {t: extract_document("x.pdf", p).text for t, p in pdfs.items()}
    words = {t: sorted(re.sub(r"[^\w]", "", x) for x in txt.split() if re.sub(r"[^\w]", "", x)) for t, txt in texts.items()}     # mismo contenido; Harvard cambia el orden de las secciones
    assert all(w == words["classic"] for w in words.values())

@pytest.mark.parametrize("template", list(TEMPLATES))
def test_docx_round_trip_real_styles_no_tables_no_images(template):
    cv = make_cv()
    data = render_docx(cv, template)
    doc = Document(io.BytesIO(data))
    assert not doc.tables and not doc.inline_shapes
    styles = [p.style.name for p in doc.paragraphs]
    assert styles[0] == "Title" and styles.count("Heading 1") == 6 and styles.count("List Bullet") == 5 and "Heading 2" in styles
    assert doc.core_properties.title == "CV - Luis Gómez" and doc.core_properties.author == "Luis Gómez"
    normal = doc.styles["Normal"]
    assert normal.font.name == TEMPLATES[template].docx_font and normal.font.size.pt == TEMPLATES[template].body_pt
    t = extract_document("cv.docx", data).text
    for needle in ("LUIS GÓMEZ", "luis@x.com", "RESUMEN PROFESIONAL", "EXPERIENCIA LABORAL", "SIEM, Splunk, MITRE ATT&CK, Python",
                   _ENTRY[template], "• Respondí a incidentes de phishing y malware", "EDUCACIÓN"):
        assert needle in t, needle
    assert check(t, []).score >= 74

def test_docx_unknown_template_falls_back_to_classic():
    assert Document(io.BytesIO(render_docx(make_cv(), "nope"))).styles["Normal"].font.name == "Times New Roman"

def test_long_cv_paginates_without_losing_text():
    exp = [{"company": f"Empresa {i}", "title": f"Cargo {i}", "start": f"{2000 + i}", "end": f"{2001 + i}",
            "bullets": [f"Logro número {j} del puesto {i} con resultados medibles y detalle suficiente para ocupar línea" for j in range(6)]} for i in range(12)]
    cv = make_cv(experience=exp)
    r = extract_document("x.pdf", render_pdf(cv, "classic"))
    assert r.pages >= 2
    for i in range(12):
        for j in range(6):
            assert f"Logro número {j} del puesto {i}" in r.text.replace("\n", " ")

def test_build_doc_skips_empty_sections_instead_of_inventing():
    d = build_doc(make_cv(education=[], certifications=[], languages=[]))
    assert [s.key for s in d.sections] == ["summary", "experience", "skills"]

def test_ats_report_of_rendered_cv_explains_each_rule():
    gaps = [KeywordGap(keyword="SIEM", status=GapStatus.HAVE), KeywordGap(keyword="Splunk", status=GapStatus.HAVE)]
    rep = check(render_text(make_cv()), gaps)
    assert {r.rule for r in rep.breakdown} == {"keywords", "sections", "contact", "dates", "chronology", "length", "characters", "stuffing"}
    assert sum(r.points for r in rep.breakdown) == pytest.approx(rep.score, abs=1)
    assert rep.keyword_coverage == 1.0 and rep.lang == "es"


def test_harvard_is_default_and_puts_dates_flush_right_with_real_text():
    import pdfplumber

    from app.cv.templates import DEFAULT_TEMPLATE
    assert DEFAULT_TEMPLATE == "harvard"
    pdf = render_pdf(make_cv())                                                   # sin plantilla => Harvard
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        page = doc.pages[0]
        words = page.extract_words()
        acme = next(w for w in words if w["text"] == "Acme")
        date = next(w for w in words if w["text"] == "Actualidad" and abs(w["top"] - acme["top"]) < 2)     # misma línea
        assert date["x1"] > page.width - 80 and acme["x0"] < 80                    # organización a la izquierda, fechas al margen derecho
        assert not page.rects or all(r["height"] < 2 for r in page.rects)          # sin cajas/tablas: solo líneas finas
    t = extract_document("x.pdf", pdf).text.split("\n")
    assert t.index("EDUCACIÓN") < t.index("EXPERIENCIA LABORAL") < t.index("HABILIDADES")
    assert t[t.index("Acme  Mar 2021 - Actualidad") + 1] == "Analista de Seguridad"      # cargo en la línea siguiente


def test_harvard_docx_uses_a_right_tab_not_a_table():
    doc = Document(io.BytesIO(render_docx(make_cv())))
    assert not doc.tables
    ent = next(p for p in doc.paragraphs if p.text.startswith("Acme"))
    assert "\t" in ent.text and ent.paragraph_format.tab_stops[0].alignment is not None
    nxt = doc.paragraphs[[p.text for p in doc.paragraphs].index(ent.text) + 1]
    assert nxt.text == "Analista de Seguridad" and nxt.runs[0].italic
