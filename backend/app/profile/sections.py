"""Segmentación del CV en secciones por encabezados (ES/EN). Sin LLM: es la base de la extracción por secciones."""
import re

from app.text import norm

# sección canónica -> sinónimos (ya sin tildes/minúsculas)
SECTION_ALIASES: dict[str, list[str]] = {
    "summary": ["perfil", "perfil profesional", "resumen", "resumen profesional", "resumen ejecutivo", "sobre mi", "acerca de mi", "objetivo",
                "objetivo profesional", "extracto", "presentacion", "summary", "professional summary", "executive summary", "profile",
                "professional profile", "about me", "about", "objective", "career objective", "personal statement", "career summary"],
    "experience": ["experiencia", "experiencia laboral", "experiencia profesional", "historial laboral", "historial profesional",
                   "trayectoria profesional", "trayectoria laboral", "experiencia de trabajo", "work experience", "professional experience",
                   "employment history", "experience", "work history", "career history", "employment", "relevant experience",
                   "experiencia relevante", "antecedentes laborales"],
    "education": ["educacion", "formacion", "formacion academica", "estudios", "estudios realizados", "educacion y formacion",
                  "formacion y educacion", "educacion formal", "education", "academic background", "academic qualifications",
                  "qualifications", "academic", "education and training", "titulos", "antecedentes academicos"],
    "skills": ["habilidades", "competencias", "aptitudes", "conocimientos", "habilidades tecnicas", "competencias tecnicas",
               "habilidades y competencias", "conocimientos tecnicos", "skills", "technical skills", "core competencies", "key skills",
               "tecnologias", "herramientas", "tools", "technologies", "areas de experiencia", "competencias clave", "habilidades clave",
               "skills and tools", "tech stack", "stack tecnologico", "skills & tools", "habilidades blandas", "soft skills"],
    "languages": ["idiomas", "lenguas", "languages", "language skills", "idiomas y niveles"],
    "certifications": ["certificaciones", "certificados", "cursos", "formacion complementaria", "licencias", "certifications",
                       "licenses", "courses", "training", "cursos y certificaciones", "certificaciones y cursos", "licenses and certifications",
                       "certificaciones y licencias", "capacitaciones", "capacitacion", "diplomados", "professional development",
                       "licencias y certificaciones", "certifications and courses", "seminarios"],
    "contact": ["contacto", "datos personales", "contact", "personal details", "personal information", "datos de contacto",
                "informacion personal", "contact information", "contact details", "informacion de contacto"],
    "other": ["proyectos", "projects", "publicaciones", "publications", "voluntariado", "volunteering", "volunteer experience", "referencias",
              "references", "intereses", "interests", "hobbies", "awards", "premios", "logros", "achievements", "reconocimientos",
              "informacion adicional", "additional information", "otros datos", "otros", "datos adicionales", "proyectos personales",
              "actividades", "asociaciones", "memberships", "disponibilidad", "referencias personales", "logros destacados",
              "experiencia voluntaria", "extracurriculares", "extracurricular activities", "patents", "patentes"],
}
_ALIAS = {a: sec for sec, al in SECTION_ALIASES.items() for a in al}

# Primera palabra que identifica la sección en encabezados largos: «EXPERIENCIA EN PROYECTOS», «FORMACIÓN ACADÉMICA Y CURSOS»,
# «HABILIDADES DE DESARROLLO». Solo se aplica a líneas cortas en mayúsculas o terminadas en «:», para no confundir frases del cuerpo.
HEAD_WORDS: dict[str, str] = {
    "experiencia": "experience", "trayectoria": "experience", "historial": "experience", "experience": "experience", "employment": "experience",
    "formacion": "education", "educacion": "education", "estudios": "education", "education": "education",
    "habilidades": "skills", "competencias": "skills", "skills": "skills", "tecnologias": "skills", "conocimientos": "skills",
    "certificaciones": "certifications", "certifications": "certifications", "idiomas": "languages", "languages": "languages",
}

def heading_of(line: str) -> str | None:
    """Sección canónica si la línea es un encabezado; si no, None."""
    raw = line.strip()
    if not raw or len(raw) > 60 or raw.startswith("•"):
        return None
    t = re.sub(r"^[\d.)\s]+(?=[A-Za-zÁÉÍÓÚÑáéíóúñ])", "", raw)           # '1. Experiencia'
    t = norm(t.strip(" :.-–—_*#|/\\"))
    t = re.sub(r"[:|]+$", "", t).strip()
    if t in _ALIAS:
        return _ALIAS[t]
    # "EXPERIENCIA LABORAL Y PROYECTOS" / "Skills & Tools": prefijo conocido en encabezado corto
    words = t.split()
    if 2 <= len(words) <= 5 and (raw.isupper() or raw.endswith(":")):
        for n in (3, 2):
            if len(words) > n and " ".join(words[:n]) in _ALIAS:
                return _ALIAS[" ".join(words[:n])]
    if 2 <= len(words) <= 5 and (raw.isupper() or raw.endswith(":")) and words[0] in HEAD_WORDS:
        # 'EXPERIENCIA VOLUNTARIA' ya está en _ALIAS como 'other' y se resolvió arriba; aquí solo caen las variantes no listadas
        return HEAD_WORDS[words[0]]
    return None

def segment(text: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """(líneas de cabecera antes del primer encabezado, [(sección, líneas)...]) en orden de aparición."""
    header: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    for ln in text.split("\n"):
        h = heading_of(ln)
        if h:
            sections.append((h, []))
        elif sections:
            sections[-1][1].append(ln)
        else:
            header.append(ln)
    return header, sections

def section_lines(sections: list[tuple[str, list[str]]], name: str) -> list[str]:
    """Une todas las apariciones de la sección (p. ej. 'Habilidades' repetida)."""
    out: list[str] = []
    for n, lines in sections:
        if n == name:
            out += lines + [""]
    while out and not out[-1].strip():
        out.pop()
    return out
