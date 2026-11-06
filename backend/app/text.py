import re
import unicodedata


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return re.sub(r"\s+", " ", "".join(c for c in s if not unicodedata.combining(c))).strip()


def contains(haystack_norm: str, term: str) -> bool:
    """Coincidencia por palabra completa ('aws' no coincide con 'laws')."""
    t = norm(term)
    return bool(t) and re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", haystack_norm) is not None


def clean_title(title: str) -> str:
    """'Agente de SOC 7 *24 ( Analista de ciberseguridad))' -> 'Agente de SOC'."""
    t = re.sub(r"\(.*", "", title)
    t = re.sub(r"\b\d+\s*[*xX/]\s*\d+\b", "", t)
    t = re.sub(r"\s+[-–|:].*$", "", t) if len(t.split()) > 6 else t
    return re.sub(r"\s+", " ", t).strip(" -–|:*") or title.strip()


def covered(haystack_norm: str, term: str) -> bool:
    """Frase exacta, o (para frases de 2+ palabras) todas sus palabras significativas presentes
    ('Framework MITRE ATT&CK' queda cubierto por 'MITRE ATT&CK')."""
    if contains(haystack_norm, term):
        return True
    stop = {"de", "del", "la", "el", "en", "y", "e", "los", "las", "con", "para", "of", "and", "the", "in", "for"}
    toks = [t for t in re.findall(r"[a-z0-9+#&.]+", norm(term)) if t not in stop and len(t) > 2]
    generic = {"framework", "herramientas", "conocimientos", "experiencia", "tecnicos", "gestion"}
    sig = [t for t in toks if t not in generic]
    return len(sig) >= 1 and len(toks) >= 2 and all(contains(haystack_norm, t) for t in sig)


# Palabras de cargo que no distinguen un área de otra: 'analista' casa con datos, finanzas y seguridad por igual.
GENERIC_ROLE_WORDS = frozenset(norm(w) for w in (
    "analista analyst ingeniero ingeniera engineer senior junior sr jr lead jefe jefa gerente manager asistente assistant auxiliar "
    "agente agent especialista specialist consultor consultora consultant coordinador coordinadora coordinator supervisor supervisora "
    "profesional professional tecnico tecnica technician director directora head principal staff associate asociado asociada "
    "de del la el en y e of and the for para con a i ii iii nivel level").split())


def specific_tokens(text: str) -> set[str]:
    """Tokens (por prefijo de 5 letras) que sí identifican el área de un cargo; sin las palabras genéricas."""
    toks = {t for t in re.findall(r"[a-z0-9+#&]{2,}", norm(text)) if t not in GENERIC_ROLE_WORDS}
    return {t[:5] for t in toks}
