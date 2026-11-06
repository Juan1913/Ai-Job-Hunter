import pytest

from app.profile import dates, parse
from app.profile.jsonfix import loads_loose
from app.profile.sections import heading_of, segment


@pytest.mark.parametrize("raw,expected", [
    ("Mar 2021", "2021-03"), ("marzo de 2021", "2021-03"), ("03/2021", "2021-03"), ("2021-03", "2021-03"), ("Sept. 2019", "2019-09"),
    ("2018", "2018"), ("Actualidad", None), ("Present", None), ("presente", None), ("", None), ("a la fecha", None),
])
def test_parse_date(raw, expected):
    assert dates.parse_date(raw) == expected

@pytest.mark.parametrize("line,start,end", [
    ("Mar 2021 - Actualidad", "2021-03", None), ("Ene 2018 – Feb 2021", "2018-01", "2021-02"), ("03/2019 - 12/2020", "2019-03", "2020-12"),
    ("2018-2020", "2018", "2020"), ("Sept. 2019 a Presente", "2019-09", None), ("Jan 2020 to Present", "2020-01", None),
    ("2015 – today", "2015", None), ("Desde 03/2016 hasta la fecha", "2016-03", None),
])
def test_find_range_variants_es_en(line, start, end):
    r = dates.find_range(line)
    assert r and (r[0], r[1]) == (start, end)

def test_find_range_rejects_non_dates_and_inverted():
    assert dates.find_range("Aumenté ventas un 40% en 2 años") is None
    assert dates.find_range("2021 - 2019") is None

def test_format_date_localized():
    assert dates.format_date("2021-03", "es") == "Mar 2021" and dates.format_date("2021-03", "en") == "Mar 2021"
    assert dates.format_date("2021-08", "es") == "Ago 2021" and dates.format_date("2021-08", "en") == "Aug 2021"
    assert dates.format_date("Marzo 2020", "en") == "Mar 2020" and dates.format_date("2019") == "2019"
    assert dates.format_date("a tiempo parcial") == "a tiempo parcial"            # texto libre del usuario: se respeta

@pytest.mark.parametrize("line,sec", [
    ("EXPERIENCIA LABORAL", "experience"), ("Work Experience", "experience"), ("Trayectoria Profesional", "experience"),
    ("Formación académica", "education"), ("EDUCATION", "education"), ("Habilidades técnicas:", "skills"), ("Core Competencies", "skills"),
    ("1. Idiomas", "languages"), ("Cursos y certificaciones", "certifications"), ("Perfil", "summary"), ("About me", "summary"),
    ("Referencias", "other"), ("EXPERIENCIA LABORAL Y PROYECTOS", "experience"),
])
def test_heading_detection_bilingual(line, sec):
    assert heading_of(line) == sec

@pytest.mark.parametrize("line", ["Gestioné un equipo de ventas", "• Experiencia en SIEM", "Analista de seguridad - Acme", "Languages: Python, Go", ""])
def test_non_headings(line):
    assert heading_of(line) is None

def test_segment_returns_header_and_ordered_sections():
    header, secs = segment("Ana Pérez\nana@x.com\nEXPERIENCIA\nCargo\nEDUCACIÓN\nTítulo")
    assert header == ["Ana Pérez", "ana@x.com"] and [s for s, _ in secs] == ["experience", "education"]

def test_phone_vs_year_ranges_and_numbers():
    assert parse.find_phone("Tel: +57 310 456 7788") == "+57 310 456 7788"
    assert parse.find_phone("(+34) 91 123 45 67") == "(+34) 91 123 45 67"
    assert parse.find_phone("Cel: 311 456 7890") == "311 456 7890"
    assert parse.find_phone("Experiencia 2015 - 2020 en ventas") is None
    assert parse.find_phone("Ventas por COP 2.800.000.000 y 12.5% de crecimiento") is None
    assert parse.find_phone("Folio 123456") is None

def test_email_regex_and_labels():
    assert parse.find_email("E-mail: ana.perez+cv@sub.empresa.com.co | web") == "ana.perez+cv@sub.empresa.com.co"
    assert parse.find_email("sin correo") is None

def test_skills_split_dedupe_and_phrases_are_not_skills():
    sp = parse.parse_skills([
        "Lenguajes: Python, Go, python", "• Excel avanzado (nivel alto)", "SQL | Power BI ; Tableau",
        "Soy una persona muy comprometida con mi trabajo y con el equipo, siempre dispuesta a aprender cosas nuevas.",
        "CI/CD, Node.js, C++, .NET"])
    names = [s.lower() for s in sp.skills]
    assert names.count("python") == 1 and "excel avanzado" in names and "power bi" in names and "tableau" in names
    assert "ci/cd" in names and "node.js" in names and "c++" in names
    assert not any(len(s.split()) > 6 for s in sp.skills)

def test_education_parsing_variants():
    e = parse.parse_education(["Ingeniería de Sistemas", "Universidad Nacional de Colombia", "2012 - 2017", "", "MBA - IE Business School (2020)"])
    assert e[0].institution == "Universidad Nacional de Colombia" and e[0].degree == "Ingeniería de Sistemas" and e[0].year == "2017"
    assert e[1].year == "2020" and e[1].degree.startswith("MBA")
    cursando = parse.parse_education(["Medicina - Universidad de Chile (2021 - Actualidad)"])
    assert cursando[0].year is None                       # en curso: no se inventa un año de egreso

def test_experience_layout_variants():
    date_first = parse.parse_experience(["Enero 2019 – Presente", "Gerente de Tienda", "Zara España, Madrid", "• Dirigí un equipo.", "Marzo 2013 – Diciembre 2018", "Subgerente", "Mango", "• Aperturas."])
    assert [(e.title, e.company, e.start, e.end) for e in date_first] == [("Gerente de Tienda", "Zara España", "2019-01", None), ("Subgerente", "Mango", "2013-03", "2018-12")]
    company_first = parse.parse_experience(["Banco de Bogotá", "Analista de Crédito", "03/2017 – 11/2019", "Evaluación de créditos."])
    assert (company_first[0].title, company_first[0].company) == ("Analista de Crédito", "Banco de Bogotá")
    at = parse.parse_experience(["Full Stack Developer at Intercom (Dublin)", "Jun 2020 - Present", "- Built X"])
    assert (at[0].title, at[0].company, at[0].end) == ("Full Stack Developer", "Intercom", None)

def test_json_repair():
    assert loads_loose('```json\n{"a": 1, "b": [1,2,],}\n```') == {"a": 1, "b": [1, 2]}
    assert loads_loose("Claro: {'skills': ['SIEM', 'AWS'], 'x': None}") == {"skills": ["SIEM", "AWS"], "x": None}
    assert loads_loose('{"entries": [{"n": 1, "title": "Jefe", "company": "Acme"}, {"n": 2, "title": "Aux') is not None       # truncado
    assert loads_loose('{skills: ["a"]}') == {"skills": ["a"]}
    assert loads_loose("nada de json") is None
