"""Extracción de texto de CVs (PDF, DOCX, TXT/MD) con orden de lectura correcto.

Decisiones (medidas en tests/test_cv_extract.py con los fixtures de tests/fixtures_cv/):
- pypdf, pdfplumber y PyMuPDF devuelven el texto en orden de dibujo/línea: en un CV de 2 columnas
  intercalan la barra lateral con el cuerpo. Por eso usamos pdfplumber (MIT; PyMuPDF es AGPL) solo
  para obtener las PALABRAS con coordenadas y reconstruimos el orden de lectura nosotros:
  detección del canal (gutter) entre columnas -> bloque izquierdo completo y luego el derecho.
- pypdf queda como respaldo si pdfplumber falla.
- DOCX: se recorre el XML en orden de documento (párrafos, tablas anidadas, cuadros de texto,
  controles de contenido) más encabezados y pies; python-docx por sí solo ignora todo eso.
- Escaneo (PDF sin texto): OCR opcional con pytesseract + binario tesseract; si no hay, se avisa
  con un mensaje claro en vez de fallar en silencio.
"""
from __future__ import annotations

import io
import re
import shutil
import unicodedata
from dataclasses import dataclass, field


class ExtractionError(Exception):
    """Error legible para el usuario (archivo dañado, protegido, escaneo sin OCR...)."""

@dataclass
class Extraction:
    text: str
    method: str                                   # "pdfplumber-columns", "pypdf", "docx-xml", "ocr", "txt"...
    pages: int = 1
    columns_detected: bool = False
    scanned: bool = False                         # el PDF no tenía capa de texto
    ocr_used: bool = False
    warnings: list[str] = field(default_factory=list)

# --------------------------------------------------------------------------- normalización

_BULLETS = "•▪◦●■□➢➤►▶‣⁃∙·◆◇❖✓✔➔→*"
_LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}
_ZERO = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)

def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    for a, b in _LIGATURES.items():
        text = text.replace(a, b)
    text = text.translate(_ZERO).replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    text = text.replace("(cid:127)", "•").replace("(cid:183)", "•")
    text = re.sub(r"\(cid:\d+\)", "", text)
    text = re.sub(r"[\x00-\x08\x0b\x0e-\x1f]", "", text)
    out = []
    for ln in text.split("\n"):
        ln = ln.replace("\t", "\u2003")                   # una tabulación es un hueco real (DOCX con fechas a la derecha)
        ln = re.sub(r"[ ]+", " ", ln)
        ln = re.sub(r" ?\u2003+ ?", "  ", ln).strip()     # hueco REAL entre segmentos (empresa ··· ciudad/fechas a la derecha) = 2 espacios; los dobles accidentales se colapsan
        # "E X P E R I E N C I A" -> "EXPERIENCIA" (encabezados con interletraje)
        if re.fullmatch(r"(?:\S ){3,}\S", ln) and ln.replace(" ", "").isalpha():
            ln = ln.replace(" ", "")
        if ln and ln[0] in _BULLETS and len(ln) > 1 and ln[1] in " \t":
            ln = "• " + ln[2:].strip()
        out.append(ln)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()

# --------------------------------------------------------------------------- PDF

@dataclass
class _Seg:
    x0: float
    x1: float
    top: float
    bottom: float
    text: str

def _lines_from_words(words: list[dict]) -> list[list[dict]]:
    """Agrupa palabras en filas por solapamiento vertical."""
    words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    rows: list[list[dict]] = []
    for w in words:
        if rows:
            r = rows[-1]
            top, bot = min(x["top"] for x in r), max(x["bottom"] for x in r)
            overlap = min(bot, w["bottom"]) - max(top, w["top"])
            if overlap > 0.5 * min(bot - top, w["bottom"] - w["top"]):
                r.append(w)
                continue
        rows.append([w])
    return rows

def _segments(rows: list[list[dict]], min_gap: float) -> list[_Seg]:
    segs: list[_Seg] = []
    for r in rows:
        r = sorted(r, key=lambda w: w["x0"])
        cur = [r[0]]
        for w in r[1:]:
            if w["x0"] - cur[-1]["x1"] >= min_gap:
                segs.append(_seg(cur))
                cur = [w]
            else:
                cur.append(w)
        segs.append(_seg(cur))
    return segs

def _seg(ws: list[dict]) -> _Seg:
    return _Seg(ws[0]["x0"], ws[-1]["x1"], min(w["top"] for w in ws), max(w["bottom"] for w in ws), " ".join(w["text"] for w in ws))

def _find_gutter(segs: list[_Seg], width: float) -> float | None:
    """x del canal entre dos columnas, o None si la página es de una sola columna."""
    if len(segs) < 8:
        return None
    lo, hi = int(width * 0.15), int(width * 0.85)
    cover = [0] * (int(width) + 2)
    for s in segs:
        for x in range(max(0, int(s.x0)), min(len(cover), int(s.x1) + 1)):
            cover[x] += 1
    thr = max(1, int(0.06 * len(segs)))
    best: tuple[int, int] | None = None
    run_start = None
    for x in range(lo, hi + 1):
        if cover[x] <= thr:
            run_start = x if run_start is None else run_start
        elif run_start is not None:
            if best is None or x - run_start > best[1] - best[0]:
                best = (run_start, x)
            run_start = None
    if run_start is not None and (best is None or hi - run_start > best[1] - best[0]):
        best = (run_start, hi)
    if not best or best[1] - best[0] < 8:
        return None
    g = (best[0] + best[1]) / 2
    left = [s for s in segs if s.x1 <= g]
    right = [s for s in segs if s.x0 >= g]
    chars = lambda ss: sum(len(s.text) for s in ss)  # noqa: E731
    total = chars(segs)
    # ambas columnas deben tener contenido sustancial (evita confundir sangrías o márgenes)
    if len(left) < 4 or len(right) < 4 or chars(left) < 0.12 * total or chars(right) < 0.12 * total:
        return None
    # Fechas/ciudades pegadas al margen derecho de cada entrada (formato Harvard, muy común) NO son una segunda columna:
    # comparten el borde derecho y son cortas, mientras que una columna real tiene el borde izquierdo común y el derecho irregular.
    edge = max(s.x1 for s in right)
    flush = sum(1 for s in right if edge - s.x1 <= 3)
    ragged_left = len({round(s.x0 / 4) for s in right}) > 0.5 * len(right)
    if flush >= 0.6 * len(right) and ragged_left and sum(len(s.text) for s in right) / len(right) < 40:
        return None
    return g

def _emit(segs: list[_Seg]) -> list[str]:
    """Líneas de una columna: segmentos de la misma fila se unen; salto de párrafo si hay hueco grande."""
    rows = _lines_from_words([{"top": s.top, "bottom": s.bottom, "x0": s.x0, "x1": s.x1, "text": s.text} for s in segs])
    lines: list[tuple[float, float, str]] = []
    for r in rows:
        r.sort(key=lambda w: w["x0"])
        lines.append((min(w["top"] for w in r), max(w["bottom"] for w in r), "\u2003".join(w["text"] for w in r)))
    if not lines:
        return []
    pitch = sorted(b - a for (a, _, _), (b, _, _) in zip(lines, lines[1:]))
    med = pitch[len(pitch) // 2] if pitch else 12
    out = [lines[0][2]]
    for prev, cur in zip(lines, lines[1:]):
        if cur[0] - prev[0] > max(1.6 * med, 1.45 * (prev[1] - prev[0]) + 4):
            out.append("")
        out.append(cur[2])
    return out

def _page_text(words: list[dict], width: float) -> tuple[str, bool]:
    if not words:
        return "", False
    sizes = sorted(w["bottom"] - w["top"] for w in words)
    med_h = sizes[len(sizes) // 2] or 10
    segs = _segments(_lines_from_words(words), min_gap=max(12.0, 1.3 * med_h))
    g = _find_gutter(segs, width)
    if g is None:
        return "\n".join(_emit(segs)), False
    out: list[str] = []
    block_l: list[_Seg] = []
    block_r: list[_Seg] = []

    def flush():
        nonlocal block_l, block_r
        blocks = [block_l, block_r]
        big = [max((x.bottom - x.top for x in b), default=0) for b in blocks]
        if big[1] > big[0] and big[1] >= 1.5 * med_h:      # la columna con el título/nombre grande se lee primero
            blocks.reverse()
        for blk in blocks:
            if blk:
                out.extend(_emit(blk))
                out.append("")
        block_l, block_r = [], []

    for s in sorted(segs, key=lambda s: (s.top, s.x0)):
        if s.x0 < g < s.x1:          # cruza el canal: línea de ancho completo (cabecera, etc.)
            flush()
            out.append(s.text)
        elif s.x1 <= g:
            block_l.append(s)
        else:
            block_r.append(s)
    flush()
    return "\n".join(out), True

def _pdf_pdfplumber(data: bytes) -> Extraction:
    import pdfplumber
    parts: list[str] = []
    cols = False
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        n = len(pdf.pages)
        for page in pdf.pages[:12]:
            words = page.extract_words(x_tolerance=1.5, y_tolerance=2, keep_blank_chars=False, use_text_flow=False)
            txt, two = _page_text(words, float(page.width))
            cols = cols or two
            parts.append(txt)
    return Extraction(normalize_text("\n\n".join(parts)), "pdfplumber-columns" if cols else "pdfplumber", n, cols)

def _pdf_pypdf(data: bytes) -> Extraction:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted and not reader.decrypt(""):
        raise ExtractionError("El PDF está protegido con contraseña: sube una copia sin protección.")
    return Extraction(normalize_text("\n".join(p.extract_text() or "" for p in reader.pages[:12])), "pypdf", len(reader.pages))

def ocr_available() -> bool:
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return shutil.which("tesseract") is not None

def _ocr_pdf(data: bytes) -> str:
    import pdfplumber
    import pytesseract
    langs = "spa+eng"
    try:
        have = set(pytesseract.get_languages())
        langs = "+".join(x for x in ("spa", "eng") if x in have) or "eng"
    except Exception:  # noqa: BLE001
        pass
    out = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages[:6]:
            out.append(pytesseract.image_to_string(page.to_image(resolution=220).original, lang=langs))
    return "\n\n".join(out)

OCR_HINT = ("Este PDF parece un escaneo (sin texto seleccionable) y el servidor no tiene OCR (instala tesseract-ocr + pytesseract). "
            "Sube una versión con texto (exportada desde Word/Google Docs) o un DOCX.")

def extract_pdf(data: bytes) -> Extraction:
    ex: Extraction | None = None
    try:
        ex = _pdf_pdfplumber(data)
    except ImportError:
        ex = None
    except Exception as e:  # noqa: BLE001
        if "assword" in repr(e) or "ncrypt" in repr(e):
            try:
                ex = _pdf_pypdf(data)       # pypdf prueba la contraseña vacía
            except ExtractionError:
                raise
            except Exception:  # noqa: BLE001
                raise ExtractionError("El PDF está protegido con contraseña: sube una copia sin protección.") from e
        ex = None
    if ex is None or len(ex.text) < 30:
        try:
            alt = _pdf_pypdf(data)
            if ex is None or len(alt.text) > len(ex.text):
                ex = alt
        except ExtractionError:
            raise
        except Exception as e:  # noqa: BLE001
            if ex is None:
                raise ExtractionError("No se pudo leer el PDF (¿está dañado?).") from e
    assert ex is not None
    if len(ex.text) < 50 * max(1, min(ex.pages, 2)) * 0.5:      # prácticamente sin texto -> escaneo
        ex.scanned = True
        if ocr_available():
            try:
                txt = normalize_text(_ocr_pdf(data))
                if len(txt) > len(ex.text):
                    ex.text, ex.method, ex.ocr_used = txt, "ocr", True
                    ex.warnings.append("Texto obtenido por OCR: revisa el perfil, puede haber errores de lectura.")
            except Exception as e:  # noqa: BLE001
                ex.warnings.append(f"El OCR falló ({e}).")
        if not ex.ocr_used:
            ex.warnings.append(OCR_HINT)
    return ex

# --------------------------------------------------------------------------- DOCX

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"

def _in_fallback_or_box(el, stop) -> bool:
    p = el.getparent()
    while p is not None and p is not stop:
        if p.tag in (_MC + "Fallback", _W + "txbxContent"):
            return True
        p = p.getparent()
    return False

def _par_text(p) -> str:
    out: list[str] = []
    for el in p.iter():
        if el.tag == _W + "t" and not _in_fallback_or_box(el, p):
            out.append(el.text or "")
        elif el.tag == _W + "tab" and not _in_fallback_or_box(el, p):
            out.append("\t" if el.getparent().tag == _W + "r" else " ")      # tabulación real (no la definición de tope en pPr) = hueco, p. ej. fechas a la derecha
        elif el.tag == _W + "br" and not _in_fallback_or_box(el, p):
            out.append("\n")
    txt = "".join(out)
    ppr = p.find(_W + "pPr")
    ps = ppr.find(_W + "pStyle") if ppr is not None else None
    styled_list = ps is not None and "list" in (ps.get(_W + "val") or "").lower()
    if ppr is not None and (ppr.find(_W + "numPr") is not None or styled_list) and txt.strip():
        txt = "• " + txt.strip()
    return txt

def _walk_block(el, out: list[str]) -> None:
    for ch in el:
        if ch.tag == _W + "p":
            out.append(_par_text(ch))
            for box in ch.iter(_W + "txbxContent"):                 # cuadros de texto (barras laterales, contactos)
                if any(a.tag == _W + "txbxContent" for a in box.iterancestors()) or any(a.tag == _MC + "Fallback" for a in box.iterancestors()):
                    continue
                out.append("")
                _walk_block(box, out)
                out.append("")
        elif ch.tag == _W + "tbl":
            for tr in ch.iter(_W + "tr"):
                if tr.getparent() is not ch:
                    continue                                        # filas de tablas anidadas se tratan al entrar en su celda
                for tc in tr.findall(_W + "tc"):
                    _walk_block(tc, out)
                    out.append("")
        elif ch.tag in (_W + "sdt", _W + "sdtContent", _W + "customXml", _W + "ins", _W + "smartTag"):
            _walk_block(ch if ch.tag != _W + "sdt" else (ch.find(_W + "sdtContent") if ch.find(_W + "sdtContent") is not None else ch), out)
        elif ch.tag in (_MC + "AlternateContent",):
            choice = ch.find(_MC + "Choice")
            _walk_block(choice if choice is not None else ch, out)

def extract_docx(data: bytes) -> Extraction:
    from docx import Document
    try:
        doc = Document(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001
        raise ExtractionError("No se pudo leer el DOCX (¿está dañado o protegido?).") from e
    head: list[str] = []
    seen: set[str] = set()
    for sec in doc.sections:
        for part in (sec.header, sec.first_page_header):
            try:
                lines: list[str] = []
                _walk_block(part._element, lines)
            except Exception:  # noqa: BLE001
                continue
            blk = "\n".join(lines).strip()
            if blk and blk not in seen:
                seen.add(blk)
                head.append(blk)
    body: list[str] = []
    _walk_block(doc.element.body, body)
    foot: list[str] = []
    for sec in doc.sections:
        try:
            lines = []
            _walk_block(sec.footer._element, lines)
            blk = "\n".join(lines).strip()
            if blk and blk not in seen and re.search(r"@|\+?\d[\d\s().-]{7,}", blk):    # solo pies con datos de contacto
                seen.add(blk)
                foot.append(blk)
        except Exception:  # noqa: BLE001
            continue
    text = normalize_text("\n".join([*head, "\n".join(body), *foot]))
    warns = []
    if len(text) < 50:
        warns.append("El DOCX casi no tiene texto (¿es una imagen incrustada?).")
    return Extraction(text, "docx-xml", 1, False, False, False, warns)

# --------------------------------------------------------------------------- texto plano

def extract_plain(data: bytes) -> Extraction:
    for enc in ("utf-8-sig", "utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp1252"):
        try:
            return Extraction(normalize_text(data.decode(enc)), "txt")
        except UnicodeDecodeError:
            continue
    return Extraction(normalize_text(data.decode("latin-1")), "txt")

# --------------------------------------------------------------------------- API pública

def extract_document(filename: str, data: bytes) -> Extraction:
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        return extract_pdf(data)
    if name.endswith(".docx"):
        return extract_docx(data)
    if name.endswith((".txt", ".md")):
        return extract_plain(data)
    raise ValueError("Formato no soportado: usa PDF, DOCX o TXT")

def extract_text(filename: str, data: bytes) -> str:
    """Compatibilidad: solo el texto. Para PDFs escaneados sin OCR devuelve '' (el API lo informa)."""
    return extract_document(filename, data).text
