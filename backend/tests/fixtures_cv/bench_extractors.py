"""Compara extractores de PDF en los fixtures: python -m tests.fixtures_cv.bench_extractors (desde backend/).
Métrica: 'intercalado' = cuántas líneas de la barra lateral (habilidades) caen DENTRO de la sección de experiencia
(0 = orden de lectura correcto). PyMuPDF solo se mide si está instalado (licencia AGPL: no es dependencia)."""
import io
import time
from pathlib import Path

from tests.fixtures_cv.data import CVS, VARIANTS

D = Path(__file__).parent

def interleave(text: str, key: str) -> int:
    """Líneas que mezclan contenido de la barra lateral y del cuerpo + líneas de habilidades dentro de la experiencia."""
    cv = CVS[key]
    side = [*cv["contact"], *cv["languages"], "CONTACTO", "HABILIDADES", "IDIOMAS", "CONTACT", "SKILLS", "LANGUAGES"]
    main = [cv["name"], cv["headline"], cv["summary"][:18], "PERFIL", "EXPERIENCIA LABORAL", "PROFILE", "WORK EXPERIENCE",
            *[b[:18] for e in cv["experience"] for b in e[4]]]
    mixed = 0
    for ln in text.split("\n"):
        in_side = any(x in ln for x in side)
        in_main = any(x in ln for x in main)
        if in_side and in_main:
            mixed += 1
    bullets = [x for e in cv["experience"] for x in e[4]]
    pos = [p for p in (text.find(x[:25]) for x in bullets) if p >= 0]
    inside = 0
    if pos:
        mid = {x.strip().lower() for x in text[min(pos):max(pos)].split("\n")}
        inside = sum(1 for sk in cv["skills"] if sk.lower() in mid)
    return mixed + inside

def main() -> None:
    from pypdf import PdfReader

    from app.profile.extract import extract_pdf
    rows = []
    for name, key, fmt, layout in VARIANTS:
        if fmt != "pdf" or layout == "scan":
            continue
        data = (D / f"{name}.pdf").read_bytes()
        res = {}
        t = time.perf_counter(); txt = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages); res["pypdf"] = (interleave(txt, key), time.perf_counter() - t)
        try:
            import pdfplumber
            t = time.perf_counter()
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                txt = "\n".join(p.extract_text() or "" for p in pdf.pages)
            res["pdfplumber"] = (interleave(txt, key), time.perf_counter() - t)
        except ImportError:
            pass
        try:
            import pymupdf
            t = time.perf_counter(); txt = "\n".join(p.get_text("text", sort=True) for p in pymupdf.open(stream=data)); res["pymupdf(sort)"] = (interleave(txt, key), time.perf_counter() - t)
        except ImportError:
            pass
        t = time.perf_counter(); txt = extract_pdf(data).text; res["propio(columnas)"] = (interleave(txt, key), time.perf_counter() - t)
        rows.append((name, res))
    for name, res in rows:
        print(name, {k: f"intercalado={v[0]} {v[1] * 1000:.0f}ms" for k, v in res.items()})

if __name__ == "__main__":
    main()
