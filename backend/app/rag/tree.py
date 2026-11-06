"""Construcción del árbol jerárquico del perfil (estilo RAPTOR, sin LLM).

    raíz (resumen del perfil)
     ├── experiencia i (resumen extractivo)  ── hojas: cargo, viñetas
     ├── habilidades (resumen)               ── hojas: cada habilidad
     └── certificaciones (resumen)           ── hojas: cada certificación
    + hojas sueltas bajo la raíz: titular y resumen del candidato

Los nodos intermedios y la raíz son extractivos (se arman con texto del propio perfil): así no cuestan llamadas
al LLM y no pueden alucinar. Aun así NUNCA se usan como evidencia citable: solo las hojas (texto literal)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.domain.profile import Profile

LEAF, MID, ROOT = 0, 1, 2


@dataclass
class RawNode:
    idx: int
    level: int
    kind: str
    ref: str
    text: str
    parent: int | None = None


def profile_hash(profile: Profile) -> str:
    """Huella del contenido relevante (las preferencias de búsqueda no cambian el árbol)."""
    data = profile.model_dump(mode="json", exclude={"preferences"})
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]


def _period(start: str | None, end: str | None) -> str:
    if not start and not end:
        return ""
    return f" ({start or '?'} - {end or 'actual'})"


def build_nodes(profile: Profile) -> list[RawNode]:
    nodes: list[RawNode] = []

    def add(level: int, kind: str, ref: str, text: str, parent: int | None) -> int:
        nodes.append(RawNode(len(nodes), level, kind, ref, text.strip(), parent))
        return len(nodes) - 1

    root = add(ROOT, "root", "root", "", None)          # el texto se completa al final
    if profile.headline.strip():
        add(LEAF, "headline", "headline", profile.headline, root)
    if profile.summary.strip():
        add(LEAF, "summary", "summary", profile.summary, root)

    for i, e in enumerate(profile.experience):
        bullets = [b for b in e.bullets if isinstance(b, str) and b.strip()]
        head = f"{e.title} en {e.company}{_period(e.start, e.end)}"
        mid = add(MID, "experience", f"experience:{i}", head + (": " + " ".join(bullets) if bullets else ""), root)
        add(LEAF, "title", f"experience:{i}:title", e.title, mid)
        for j, b in enumerate(bullets):
            add(LEAF, "bullet", f"experience:{i}:bullet:{j}", b, mid)

    skills = [s.name for s in profile.skills if s.name.strip()]
    if skills:
        mid = add(MID, "skills", "skills", "Habilidades: " + ", ".join(skills), root)
        for k, s in enumerate(skills):
            add(LEAF, "skill", f"skill:{k}", s, mid)

    certs = [c for c in profile.certifications if c.strip()]
    if certs:
        mid = add(MID, "certs", "certs", "Certificaciones: " + ", ".join(certs), root)
        for k, c in enumerate(certs):
            add(LEAF, "cert", f"cert:{k}", c, mid)

    parts = [profile.headline, profile.summary[:400]]
    if profile.experience:
        parts.append("Experiencia: " + "; ".join(f"{e.title} ({e.company})" for e in profile.experience[:6]))
    if skills:
        parts.append("Habilidades: " + ", ".join(skills[:15]))
    if certs:
        parts.append("Certificaciones: " + ", ".join(certs[:6]))
    nodes[root].text = "\n".join(p.strip() for p in parts if p and p.strip()) or (profile.full_name or "perfil")
    return nodes
