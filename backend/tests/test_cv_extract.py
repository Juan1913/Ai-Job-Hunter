import io
import re
from pathlib import Path

import pytest

from app.profile import extract as ex
from app.profile.extract import ExtractionError, extract_document, extract_text, normalize_text
from tests.fixtures_cv.bench_extractors import interleave
from tests.fixtures_cv.data import CVS, VARIANTS

FIX = Path(__file__).parent / "fixtures_cv"
READABLE = [(n, k, f, lay) for n, k, f, lay in VARIANTS if lay != "scan"]

def _read(name, fmt):
    return extract_document(f"{name}.{fmt}", (FIX / f"{name}.{fmt}").read_bytes())

@pytest.mark.parametrize("name,key,fmt,layout", READABLE, ids=[r[0] for r in READABLE])
def test_extracts_ground_truth_from_every_layout(name, key, fmt, layout):
    t = _read(name, fmt).text
    truth = CVS[key]["truth"]
    assert truth["email"] in t
    assert re.sub(r"\D", "", truth["phone"]) in re.sub(r"\D", "", t)
    for company in truth["companies"]:
        assert company in t
    for skill in truth["skills"]:
        assert skill in t
    for sec in truth["sections"]:
        assert sec in t.upper()
    for e in CVS[key]["experience"]:
        for bullet in e[4]:
            assert bullet[:30] in t.replace("\n", " ").replace("• ", ""), bullet

@pytest.mark.parametrize("name,key", [("ventas_es_2col", "ventas_es"), ("desarrollo_en_2col", "desarrollo_en"), ("enfermeria_es_2col", "enfermeria_es")])
def test_two_column_pdf_reading_order_beats_naive_extractors(name, key):
    """pypdf intercala la barra lateral con el cuerpo; nuestro extractor lee cada columna completa."""
    from pypdf import PdfReader
    data = (FIX / f"{name}.pdf").read_bytes()
    naive = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)
    ours = extract_document(f"{name}.pdf", data)
    assert ours.columns_detected and ours.method == "pdfplumber-columns"
    assert interleave(ours.text, key) == 0
    assert interleave(naive, key) > 0                      # la línea base real sí mezcla columnas

def test_single_column_pdf_is_not_split_into_columns():
    r = _read("contabilidad_es_1col", "pdf")
    assert not r.columns_detected
    assert r.text.index("PERFIL") < r.text.index("EXPERIENCIA") < r.text.index("EDUCACI")

def test_docx_table_layout_reads_cell_by_cell_not_interleaved():
    t = _read("enfermeria_es_tabla", "docx").text
    exp = t[t.index("EXPERIENCIA"):t.index("EDUCACI")]
    assert "Hospital La Fe" in exp and "Quirónsalud" in exp
    assert not any(s in exp.split("\n") for s in CVS["enfermeria_es"]["skills"])      # nada de la barra lateral dentro de la experiencia

def test_docx_textbox_and_header_are_extracted():
    t = _read("seguridad_en_textbox", "docx").text
    assert "priya.raman@securemail.com" in t.split("\n")[0]       # el contacto vive en el encabezado de página
    assert "Splunk" in t and "Hindi (fluent)" not in t.split("SKILLS")[0]       # barra lateral (cuadro de texto) incluida
    assert "MITRE ATT&CK" in t

def test_docx_bullets_are_marked_and_python_docx_alone_would_lose_them():
    from docx import Document
    d = _read("contabilidad_es_1col_docx", "docx").text
    assert "• NIIF" in d
    plain = "\n".join(p.text for p in Document(str(FIX / "contabilidad_es_1col_docx.docx")).paragraphs)
    assert "•" not in plain

def test_scanned_pdf_detected_with_clear_message_when_no_ocr(monkeypatch):
    monkeypatch.setattr(ex, "ocr_available", lambda: False)
    r = _read("ventas_es_escaneo", "pdf")
    assert r.scanned and not r.ocr_used and r.text == ""
    assert "escaneo" in r.warnings[0] and "tesseract" in r.warnings[0]

def test_scanned_pdf_uses_ocr_when_available(monkeypatch):
    monkeypatch.setattr(ex, "ocr_available", lambda: True)
    monkeypatch.setattr(ex, "_ocr_pdf", lambda data: "Carolina Ruiz Medina\ncarolina.ruiz@correo.co\n" + "Experiencia en ventas B2B. " * 5)
    r = _read("ventas_es_escaneo", "pdf")
    assert r.scanned and r.ocr_used and r.method == "ocr" and "carolina.ruiz@correo.co" in r.text
    assert any("OCR" in w for w in r.warnings)

def test_ocr_failure_degrades_to_hint(monkeypatch):
    monkeypatch.setattr(ex, "ocr_available", lambda: True)
    def boom(data):
        raise RuntimeError("sin idioma spa")
    monkeypatch.setattr(ex, "_ocr_pdf", boom)
    r = _read("ventas_es_escaneo", "pdf")
    assert r.text == "" and any("OCR falló" in w for w in r.warnings) and any("escaneo" in w for w in r.warnings)

def test_corrupt_and_unsupported_files():
    with pytest.raises(ExtractionError):
        extract_document("x.pdf", b"%PDF-1.4 esto no es un pdf" * 5)
    with pytest.raises(ExtractionError):
        extract_document("x.docx", b"no es un zip")
    with pytest.raises(ValueError):
        extract_document("x.exe", b"MZ")
    assert extract_text("x.txt", "Año ñandú".encode("cp1252")) == "Año ñandú"       # TXT en Windows-1252
    assert extract_text("x.md", b"\xef\xbb\xbfHola mundo") == "Hola mundo"           # BOM

def test_normalize_text_cleans_pdf_artifacts():
    t = normalize_text("E X P E R I E N C I A\n•  Gestión de ﬁnanzas  y  caja\n➤ Otro punto\n(cid:127) otro\n\n\n\nfin​")
    lines = t.split("\n")
    assert lines[0] == "EXPERIENCIA"
    assert lines[1] == "• Gestión de finanzas y caja" and lines[2] == "• Otro punto"
    assert "(cid" not in t and "​" not in t and "\n\n\n" not in t

def test_encrypted_pdf_with_user_password_gives_clear_message():
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter()
    for p in PdfReader(str(FIX / "contabilidad_es_1col.pdf")).pages:
        w.add_page(p)
    w.encrypt("secreto")
    buf = io.BytesIO()
    w.write(buf)
    with pytest.raises(ExtractionError, match="contraseña"):
        extract_document("x.pdf", buf.getvalue())
