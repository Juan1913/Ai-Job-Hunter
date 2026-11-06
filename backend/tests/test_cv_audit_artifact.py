"""Verificación del artefacto real: se extrae la capa de texto del PDF/DOCX y se vuelve a puntuar con el checker ATS."""
import pytest

from app.cv import artifact as art
from app.cv.artifact import verify_artifact
from app.cv.render import render_text
from app.domain.tailoring import GapStatus, KeywordGap, TailoredCV
from tests.test_cv_audit import GAPS, faithful


def _cv() -> TailoredCV:
    cv = faithful()
    cv.text = render_text(cv)
    return cv


@pytest.mark.parametrize("fmt", ["pdf", "docx"])
@pytest.mark.parametrize("template", ["harvard", "classic", "compact", "modern"])
def test_real_file_keeps_text_and_score(fmt, template):
    rep = verify_artifact(_cv(), GAPS[:3], template, fmt)
    assert rep.ok, rep.issues
    assert rep.fidelity >= 0.97 and rep.order_ok and not rep.lost_keywords
    assert rep.artifact_score >= rep.logical_score - 3 and rep.pages >= 1
    assert rep.template_used == template and not rep.forced_safe_template


def test_pdf_reports_extraction_method_and_pages():
    rep = verify_artifact(_cv(), GAPS[:3], "classic", "pdf")
    assert rep.extraction_method.startswith("pdf") and rep.pages == 1


def test_lost_keyword_in_the_file_is_reported():
    """Un carácter que el PDF (fuentes estándar) no puede dibujar: el texto lógico lo tiene, el archivo no."""
    cv = _cv()
    cv.skills.append("数据分析")
    cv.text = render_text(cv)
    rep = verify_artifact(cv, [*GAPS[:2], KeywordGap(keyword="数据分析", status=GapStatus.HAVE)], "classic", "pdf")
    assert not rep.ok and "数据分析" in rep.lost_keywords and rep.issues
    assert rep.tried and {t["template"] for t in rep.tried} >= {"classic", "compact"}      # probó las demás plantillas antes de rendirse


def test_forces_safe_template_when_the_requested_one_breaks_the_text(monkeypatch):
    real = art.render_artifact

    def broken_for_compact(cv, template, fmt, lang=None):
        if template == "compact":                          # esta plantilla 'pierde' una sección entera
            cv = cv.model_copy(deep=True)
            cv.skills, cv.summary = [], ""
        return real(cv, template, fmt, lang)
    monkeypatch.setattr(art, "render_artifact", broken_for_compact)
    rep = verify_artifact(_cv(), GAPS[:3], "compact", "pdf")
    assert rep.ok and rep.forced_safe_template and rep.template == "compact" and rep.template_used == "harvard"
    assert any("fuerza la plantilla" in i for i in rep.issues)


def test_finalize_exposes_artifact_score_and_audit(client):
    from app.cv.finalize import finalize_cv
    from tests.test_cv_audit import GAPS as G
    from tests.test_cv_audit import JOB, PROFILE
    cv = finalize_cv(faithful(), JOB, G, None, profile=PROFILE)
    assert cv.artifact and cv.artifact["ok"] and cv.ats.artifact_score == cv.artifact["artifact_score"]
    assert cv.audit and cv.audit["verdict"] in ("ok", "warn") and cv.review_required is False
