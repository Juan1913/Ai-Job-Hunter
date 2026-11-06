"""Plantillas de CV. Todas son ATS-seguras: UNA columna, fuentes estándar, texto real seleccionable,
sin tablas, imágenes, iconos ni cuadros de texto. Solo cambian tipografía, tamaños, espaciado y color de acento."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Template:
    name: str
    label: str
    description: str
    # PDF (fuentes estándar de PDF: no se incrustan y todos los lectores las tienen)
    pdf_font: str
    pdf_bold: str
    pdf_italic: str
    # DOCX
    docx_font: str
    body_pt: float
    name_pt: float
    heading_pt: float
    leading: float                      # interlineado como factor del cuerpo
    margin_cm: float
    section_gap_pt: float
    name_align: str                     # left | center
    accent: str                         # color hex de nombre/encabezados ('000000' = sin color)
    rule: bool                          # línea fina bajo los encabezados
    layout: str = "classic"             # classic | harvard (organización en negrita + fechas a la derecha, cargo en cursiva, sin resumen)
    order: tuple = ("summary", "skills", "experience", "education", "certifications", "languages")
    show_summary: bool = True

HARVARD_ORDER = ("summary", "education", "experience", "skills", "certifications", "languages")

TEMPLATES: dict[str, Template] = {
    "harvard": Template("harvard", "Harvard", "Formato de la oficina de carreras de Harvard: una columna, Educación primero, orden cronológico inverso, "
                        "organización en negrita con fechas a la derecha, cargo en cursiva, sin foto; resumen breve opcional.",
                        "Times-Roman", "Times-Bold", "Times-Italic", "Times New Roman", 10.5, 16, 11, 1.18, 1.9, 8, "center", "000000", True,
                        layout="harvard", order=HARVARD_ORDER),
    "classic": Template("classic", "Clásica", "Serif tradicional, nombre centrado, encabezados con línea.",
                        "Times-Roman", "Times-Bold", "Times-Italic", "Times New Roman", 10.5, 18, 11.5, 1.22, 2.0, 9, "center", "000000", True),
    "compact": Template("compact", "Compacta", "Sans-serif densa para caber en una página.",
                        "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Arial", 9.5, 15, 10, 1.15, 1.5, 6, "left", "000000", True),
    "modern": Template("modern", "Moderna sobria", "Sans-serif con un único color de acento.",
                       "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Calibri", 10.5, 20, 11, 1.25, 1.9, 10, "left", "1F3A5F", False),
}

DEFAULT_TEMPLATE = "harvard"

def get_template(name: str | None) -> Template:
    return TEMPLATES.get((name or DEFAULT_TEMPLATE).lower(), TEMPLATES[DEFAULT_TEMPLATE])
