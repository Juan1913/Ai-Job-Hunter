from app.ats.checker import check, explain
from app.cv.render import render_text
from app.domain.tailoring import GapStatus, KeywordGap
from tests.test_cv_render import make_cv

HAVE = lambda k: KeywordGap(keyword=k, status=GapStatus.HAVE)  # noqa: E731
GAPS = [HAVE("SIEM"), HAVE("Splunk")]

def rule(rep, name):
    return next(r for r in rep.breakdown if r.rule == name)

_VOCAB = ("coordiné reportes semanales auditorías inventarios proveedores capacitaciones presupuestos indicadores clientes protocolos "
          "turnos cobranzas negociaciones campañas documentación calidad seguimiento conciliaciones despachos soporte evaluaciones").split()

def padded(cv_text: str, n: int = 300) -> str:
    """Agrega cuerpo variado para que el largo no distorsione la regla que se prueba."""
    lines = []
    for i in range(n // 10):
        w = [_VOCAB[(i * 3 + k) % len(_VOCAB)] + ("" if (i + k) % 5 else f"{i}") for k in range(10)]
        lines.append("- " + " ".join(w))
    return cv_text + "\n".join(lines) + "\n"

def test_clean_cv_scores_high_and_explains_every_point():
    rep = check(padded(render_text(make_cv())), GAPS)
    assert rep.score >= 95 and rep.issues == [] and rep.warnings == []
    assert sum(r.max_points for r in rep.breakdown) == 100
    lines = explain(rep)
    assert len(lines) == 8 and lines[0].startswith("keywords: 60/60")

def test_sections_accepted_in_both_languages_and_reported_in_the_cv_language():
    es = check(render_text(make_cv()), [])
    assert not any("sección" in i for i in es.issues)
    en = check(render_text(make_cv(summary="Security analyst with 4 years of experience in SIEM monitoring and incident response.")), [])
    assert en.lang == "en" and not any("sección" in i for i in en.issues)
    broken = render_text(make_cv()).replace("EXPERIENCIA LABORAL", "LO QUE HE HECHO").replace("HABILIDADES", "COSAS")
    rep = check(broken, [])
    assert "falta la sección estándar 'EXPERIENCIA LABORAL'" in rep.issues and "falta la sección estándar 'HABILIDADES'" in rep.issues
    assert rule(rep, "sections").points == 5
    alt = check("PERFIL\nx\nCOMPETENCIAS\ny\nEXPERIENCIA PROFESIONAL\nz\nFORMACIÓN ACADÉMICA\nw\nluis@x.com", [])
    assert not any("sección" in i for i in alt.issues)           # sinónimos habituales también valen

def test_legacy_single_line_headings_and_leak_detection_still_work():
    gaps = [HAVE("SIEM"), KeywordGap(keyword="AWS", status=GapStatus.MISSING)]
    r = check("PROFESSIONAL SUMMARY SKILLS WORK EXPERIENCE EDUCATION luis@x.com SIEM AWS " + "palabra " * 160, gaps)
    assert any("AWS" in i and "no lo ha confirmado" in i for i in r.issues)
    assert not any("sección" in i for i in r.issues)

def test_contact_rules():
    t = render_text(make_cv())
    no_phone = check(t.replace("+57 300 123 4567", "").replace(" |  |", " |"), [])
    assert "no hay teléfono legible" in no_phone.warnings and rule(no_phone, "contact").points == 5
    no_email = check(t.replace("luis@x.com", ""), [])
    assert "no hay email legible" in no_email.issues and rule(no_email, "contact").points == 3

def test_dates_missing_ambiguous_and_chronology():
    base = render_text(make_cv(), template="classic")
    nodates = base.replace(" (Mar 2021 - Actualidad)", "").replace(" (Ene 2018 - Feb 2021)", "")
    r = check(nodates, [])
    assert any("fechas legibles" in w for w in r.warnings) and rule(r, "dates").points == 0
    amb = check(base.replace("(Mar 2021 - Actualidad)", "(03/21 - hoy)"), [])
    assert any("ambiguo" in w for w in amb.warnings) or any("fechas" in w for w in amb.warnings)
    swapped = base.replace("(Mar 2021 - Actualidad)", "(Ene 2010 - Feb 2012)")
    r2 = check(swapped, [])
    assert any("cronológico" in w for w in r2.warnings) and rule(r2, "chronology").points == 0
    assert rule(check(base, []), "chronology").ok

def test_problematic_characters_penalized():
    base = render_text(make_cv())
    for bad, why in (("🚀 Listo", "emojis"), ("➢ punto", "viñetas"), ("col1\tcol2", "tabla"), ("texto � roto", "dañados")):
        r = check(base + "\n" + bad, [])
        assert rule(r, "characters").points < 4, why
        assert any(w in " ".join(r.issues) for w in ("emojis", "viñetas", "tabla", "dañados")), why

def test_keyword_stuffing_detected_but_natural_repetition_is_fine():
    base = render_text(make_cv())
    stuffed = base + "\n" + "SIEM Splunk SIEM Splunk SIEM Splunk SIEM Splunk " * 3
    r = check(stuffed, GAPS)
    assert any("relleno" in i for i in r.issues) and rule(r, "stuffing").points == 0
    assert not any("relleno" in i for i in check(padded(base), GAPS).issues)
    long_list = base + "\n" + ", ".join(f"Skill{i}" for i in range(60))
    assert any("elementos" in i for i in check(long_list, []).issues)

def test_length_bounds_keep_legacy_messages():
    assert any(i.startswith("CV muy corto") for i in check("EXPERIENCIA luis@x.com corto", []).issues)
    assert any(i.startswith("CV muy largo") for i in check("palabra " * 1300, []).issues)

def test_score_rises_when_supported_keywords_are_included():
    gaps = [HAVE("SIEM"), HAVE("Splunk"), HAVE("Python"), HAVE("MITRE ATT&CK")]
    full = check(padded(render_text(make_cv())), gaps)
    thin = make_cv(skills=["Python"], summary="Analista de seguridad con 4 años de experiencia.",
                   experience=[{"company": "Acme", "title": "Analista", "start": "2021", "end": None, "bullets": ["Atendí usuarios"]}])
    less = check(padded(render_text(thin)), gaps)
    assert full.score > less.score and full.keyword_coverage == 1.0 and less.keyword_coverage < 1.0
    assert rule(full, "keywords").points == 60 and rule(less, "keywords").points < 60
