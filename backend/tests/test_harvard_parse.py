"""Un CV en formato Harvard (organización y ciudad a la izquierda/derecha, cargo en cursiva, fechas al margen derecho) debe leerse completo."""
import io

from app.profile.builder import build_profile_with_report
from app.profile.extract import extract_document


def _harvard_pdf() -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    W, H = letter
    y = H - 50

    def line(left, right="", font="Times-Roman", size=10.5, x=54):
        nonlocal y
        c.setFont(font, size)
        c.drawString(x, y, left)
        if right:
            c.setFont("Times-Roman", size)
            c.drawRightString(W - 54, y, right)
        y -= 13

    def head(t):
        nonlocal y
        y -= 6
        c.setFont("Times-Bold", 11)
        c.drawString(54, y, t)
        c.line(54, y - 3, W - 54, y - 3)
        y -= 16
    c.setFont("Times-Bold", 16)
    c.drawCentredString(W / 2, y, "JANE Q. DOE")
    y -= 16
    c.setFont("Times-Roman", 10)
    c.drawCentredString(W / 2, y, "Cambridge, MA 02138 • jane.doe@college.harvard.edu • (617) 555-0123")
    y -= 10
    head("EDUCATION")
    line("Harvard University", "Cambridge, MA", "Times-Bold")
    line("Bachelor of Arts in Computer Science, cum laude", "May 2022")
    head("EXPERIENCE")
    line("Google", "Mountain View, CA", "Times-Bold")
    line("Software Engineering Intern", "June 2021 – August 2021", "Times-Italic")
    line("• Built a Python service that cut build times 30% across 40 repositories", x=60)
    line("• Led migration of 12 microservices to Kubernetes on GCP", x=60)
    y -= 4
    line("Harvard Computer Science Department", "Cambridge, MA", "Times-Bold")
    line("Teaching Fellow, CS50", "September 2020 – May 2022", "Times-Italic")
    line("• Taught weekly sections for 25 students and graded problem sets in C and Python", x=60)
    head("LEADERSHIP & ACTIVITIES")
    line("Harvard Computer Society", "Cambridge, MA", "Times-Bold")
    line("President", "2020 – 2022", "Times-Italic")
    line("• Organized hackathon with 300 participants and 15 sponsors", x=60)
    head("SKILLS & INTERESTS")
    line("Technical: Python, Java, C, SQL, React, Docker, Kubernetes, AWS")
    line("Languages: English (native), Spanish (fluent)")
    line("Interests: Chess, rock climbing")
    c.save()
    return buf.getvalue()


def test_right_aligned_dates_and_cities_are_not_mistaken_for_a_second_column():
    r = extract_document("cv.pdf", _harvard_pdf())
    assert not r.columns_detected
    lines = r.text.split("\n")
    assert any(ln.startswith("Software Engineering Intern") and "June 2021" in ln for ln in lines)     # fecha en SU línea


def test_harvard_cv_is_parsed_field_by_field():
    p = build_profile_with_report(extract_document("cv.pdf", _harvard_pdf()).text, None).profile
    assert p.full_name == "Jane Q. Doe" and p.email == "jane.doe@college.harvard.edu" and p.location == "Cambridge, MA"
    got = [(e.company, e.title, e.start, e.end, len(e.bullets)) for e in p.experience]
    assert got == [("Google", "Software Engineering Intern", "2021-06", "2021-08", 2),
                   ("Harvard Computer Science Department", "Teaching Fellow", "2020-09", "2022-05", 1),
                   ("Harvard Computer Society", "President", "2020", "2022", 1)]
    assert [(e.institution, e.year) for e in p.education] == [("Harvard University", "2022")]
    names = [s.name for s in p.skills]
    assert names == ["Python", "Java", "C", "SQL", "React", "Docker", "Kubernetes", "AWS"]       # sin ciudades, fechas, idiomas ni intereses
    assert p.languages == ["English (native)", "Spanish (fluent)"]


def test_our_own_harvard_export_reads_back_the_same_in_pdf_and_docx():
    from app.cv.pdf import render_pdf
    from app.cv.render import render_docx
    from tests.test_cv_render import make_cv
    cv = make_cv()
    for name, data in (("cv.pdf", render_pdf(cv)), ("cv.docx", render_docx(cv))):
        p = build_profile_with_report(extract_document(name, data).text, None).profile
        assert [(e.company, e.title, e.start, e.end) for e in p.experience] == [("Acme", "Analista de Seguridad", "2021-03", None), ("Globex", "Soporte TI", "2018-01", "2021-02")], name
        assert p.education and p.education[0].institution == "UNAL" and p.education[0].year == "2018", name


def test_city_glued_to_the_organisation_and_ampersand_titles():
    from app.profile.parse import _strip_city_country, decide_title_company
    assert _strip_city_country("Acme Agencia Medellín, Colombia") == "Acme Agencia"
    assert _strip_city_country("Universidad del Valle Cali, Colombia") == "Universidad del Valle"
    assert _strip_city_country("Globex Inc. San Francisco, CA") == "Globex Inc."
    assert _strip_city_country("Analista de datos") == "Analista de datos"                  # sin ciudad: intacto
    t, c, *_ = decide_title_company(["Nube Tecnológica", "Desarrollador de Software & DevSecOps SOC"])
    assert (c, t) == ("Nube Tecnológica", "Desarrollador de Software & DevSecOps SOC")      # '&' y 'tecnológica' no invierten cargo/empresa
    assert decide_title_company(["Johnson & Johnson", "Analista"])[1] == "Johnson & Johnson"


def test_standard_split_across_lines_is_rejoined_in_skills():
    from app.profile.parse import parse_skills
    assert "ISO 27001" in parse_skills(["Seguridad: AppSec, hardening, SOC, ISO", "27001"]).skills
