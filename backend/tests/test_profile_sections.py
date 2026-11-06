

def test_long_uppercase_headings_resolve_by_their_first_word():
    """Visto en CVs reales de desarrolladores: 'EXPERIENCIA EN PROYECTOS' dejaba la experiencia sin leer (0 puestos)."""
    from app.profile.sections import heading_of
    assert heading_of("EXPERIENCIA EN PROYECTOS") == "experience"
    assert heading_of("TRAYECTORIA EN DESARROLLO DE SOFTWARE") == "experience"
    assert heading_of("FORMACIÓN ACADÉMICA Y CURSOS") == "education"
    assert heading_of("HABILIDADES DE DESARROLLO") == "skills"
    assert heading_of("EXPERIENCIA VOLUNTARIA") == "other"                  # el alias exacto manda
    assert heading_of("Experiencia en proyectos de software con equipos distribuidos y metodologías ágiles") is None   # frase del cuerpo
    assert heading_of("Experiencia en Java") is None                         # no es mayúscula ni termina en ':'
