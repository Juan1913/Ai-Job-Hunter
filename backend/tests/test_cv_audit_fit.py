"""Ajuste a páginas: se MIDE con reportlab y se recorta por relevancia, sin perder palabras clave únicas ni la experiencia más reciente."""
import pytest

from app.cv.fit import fit_cv, fit_letter, measure_letter_pages, measure_pages, score_bullets
from app.cv.render import render_text
from app.domain.tailoring import GapStatus, KeywordGap, TailoredCV

GAPS = [KeywordGap(keyword="SIEM", status=GapStatus.HAVE), KeywordGap(keyword="Splunk", status=GapStatus.HAVE),
        KeywordGap(keyword="Active Directory", status=GapStatus.HAVE), KeywordGap(keyword="Kubernetes", status=GapStatus.MISSING)]


def long_cv(n_exp=6, n_bullets=8, unique_old=True) -> TailoredCV:
    exp = []
    for i in range(n_exp):
        bl = []
        for k in range(n_bullets):
            if k % 3 == 0:
                bl.append(f"Gestioné {k + 2} proyectos de seguridad con SIEM y Splunk logrando {10 + k}% menos incidentes en el área {i} del negocio con apoyo del equipo regional")
            elif k % 3 == 1:
                bl.append(f"Coordiné actividades operativas número {i}-{k} con proveedores y áreas internas para mantener la continuidad del servicio en la región")
            else:
                bl.append(f"Elaboré reportes mensuales {i}-{k} de gestión para la gerencia y los líderes de cada sede de la compañía")
        exp.append({"company": f"Empresa {chr(65 + i)}", "title": f"Analista nivel {i}", "start": str(2024 - 3 * i - 3), "end": None if i == 0 else str(2024 - 3 * i), "bullets": bl})
    if unique_old:                     # la ÚNICA mención de Active Directory está en la experiencia más antigua
        exp[-1]["bullets"][min(5, n_bullets - 1)] = "Administré cuentas de Active Directory"
    cv = TailoredCV(headline="Analista de seguridad", summary="Analista con experiencia en seguridad y operaciones. " * 2, skills=["SIEM", "Splunk", "Python"], experience=exp,
                    education=[{"institution": "UNAL", "degree": "Ingeniería", "year": "2010"}], languages=["Español"],
                    contact={"full_name": "Luis Gómez", "email": "l@x.com", "phone": "+57 300 123 4567", "location": "Bogotá"}, lang="es")
    cv.text = render_text(cv)
    return cv


def test_pages_are_measured_not_guessed():
    short = long_cv(2, 3)
    big = long_cv(8, 10)
    assert measure_pages(short, "classic") == 1
    assert measure_pages(big, "classic") >= 3 and measure_pages(big, "compact") >= 2
    # la plantilla densa ocupa menos o igual que la clásica, medido con el mismo motor
    assert measure_pages(big, "compact") <= measure_pages(big, "classic")


def test_already_fitting_cv_is_untouched():
    cv = long_cv(2, 3)
    out, res = fit_cv(cv, 2, "classic", GAPS)
    assert res.fits and not res.trimmed and out.experience == cv.experience and res.pages_before == res.pages_after == 1


@pytest.mark.parametrize("target", [1, 2])
@pytest.mark.parametrize("template", ["classic", "compact"])
def test_long_profile_is_trimmed_to_target_and_reports_why(target, template):
    cv = long_cv(8, 10)
    before = measure_pages(cv, template)
    out, res = fit_cv(cv, target, template, GAPS)
    assert res.pages_before == before and before > target
    assert measure_pages(out, template) == res.pages_after
    if res.fits:
        assert res.pages_after <= target
    assert res.trimmed and all(t.reason and t.text for t in res.trimmed)
    # lo que se quitó no aparece y lo que queda es texto original (solo se resta)
    original = {b for e in cv.experience for b in e["bullets"]}
    kept = {b for e in out.experience for b in e["bullets"]}
    assert kept <= original and len(kept) == len(original) - len(res.trimmed)


def test_most_recent_experience_and_every_company_survive():
    cv = long_cv(8, 10)
    out, res = fit_cv(cv, 1, "compact", GAPS)
    assert [e["company"] for e in out.experience] == [e["company"] for e in cv.experience]       # nunca se elimina una experiencia
    assert len(out.experience[0]["bullets"]) >= 2 and all(len(e["bullets"]) >= 1 for e in out.experience)


def test_unique_mandatory_keyword_is_never_trimmed():
    cv = long_cv(8, 10, unique_old=True)
    cv.skills = ["SIEM", "Splunk", "Python"]            # Active Directory solo vive en una viñeta de la experiencia más antigua
    cv.text = render_text(cv)
    out, res = fit_cv(cv, 1, "classic", GAPS)
    assert "Active Directory" in render_text(out) and any("Active Directory" in b for b in out.experience[-1]["bullets"])
    assert any("Active Directory" in p for p in res.protected) or not any("Active Directory" in t.text for t in res.trimmed)


def test_scoring_prefers_relevant_numeric_recent_unique_bullets():
    cv = long_cv(3, 6)
    sc = {(s["exp"], s["idx"]): s["score"] for s in score_bullets(cv, GAPS)}
    assert sc[(0, 0)] > sc[(0, 2)]          # con palabras clave y cifras > genérica
    assert sc[(0, 0)] > sc[(2, 0)]          # misma redacción pero más reciente


def test_duplicates_are_cut_first():
    cv = long_cv(2, 3, unique_old=False)
    dup = cv.experience[0]["bullets"][1]
    cv.experience[0]["bullets"] += [dup + " ", dup.replace("Coordiné", "Coordiné y")]
    sc = {s["text"]: s["score"] for s in score_bullets(cv, GAPS)}
    assert sc[dup.replace("Coordiné", "Coordiné y")] < sc[cv.experience[0]["bullets"][0]]


def test_cover_letter_fits_one_page_and_keeps_greeting_and_closing():
    para = " ".join(f"Tengo experiencia relevante número {i} en proyectos de seguridad con SIEM y Splunk para equipos grandes." for i in range(30))
    text = "Estimado equipo de selección,\n\n" + para + "\n\n" + para + "\n\nQuedo atento a su respuesta.\n\nLuis Gómez"
    assert measure_letter_pages(text) >= 2
    out, res = fit_letter(text, ["SIEM", "Splunk"], "classic", 1)
    assert res.fits and res.pages_after == 1 and res.trimmed
    assert out.startswith("Estimado equipo") and out.endswith("Luis Gómez")
