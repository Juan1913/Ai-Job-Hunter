"""Competencias de comportamiento y su detección (determinista, ES/EN) en la oferta y en las viñetas reales del perfil.

Un 'disparador' es una raíz de palabra (sin tildes): 'colabor' cubre colaboré/colaboración/collaborate. Se cuentan sobre la oferta
(qué competencias pide) y sobre cada viñeta (qué competencia ilustra). Nada aquí inventa contenido: solo clasifica texto existente."""
from __future__ import annotations

import re

from app.text import norm

# clave -> (etiqueta ES, etiqueta EN, disparadores)
COMPETENCIES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "leadership": ("Liderazgo", "Leadership", ("lider", "dirig", "mentor", "supervis", "gestion de equip", "responsable de equip", "lead", "manag", "head of", "coach")),
    "teamwork": ("Trabajo en equipo", "Teamwork", ("equipo", "colabor", "coopera", "multidisciplin", "team", "collabor", "cross-functional", "coordin")),
    "communication": ("Comunicación", "Communication", ("comunic", "present", "reporte", "informe", "document", "negoci", "stakeholder", "capacit", "report", "communicat", "train")),
    "problem_solving": ("Resolución de problemas", "Problem solving", ("resolv", "soluci", "diagnost", "problema", "troubleshoot", "solve", "incident", "contuv", "contener", "mitig", "investig")),
    "results": ("Orientación a resultados", "Results orientation", ("reduj", "aument", "mejor", "logr", "increment", "optimiz", "ahorr", "meta", "objetivo", "improv", "reduc", "increas", "achiev", "deliver", "kpi")),
    "customer": ("Orientación al cliente", "Customer focus", ("client", "usuario", "soporte", "atenci", "servicio", "customer", "support", "user", "stakeholder")),
    "initiative": ("Iniciativa e innovación", "Initiative", ("implement", "automat", "propus", "disen", "lanz", "desarroll", "cre", "built", "design", "launch", "initiat", "proactiv", "innov")),
    "planning": ("Planificación y organización", "Planning", ("planific", "organiz", "prioriz", "cronograma", "gestion", "plan", "schedul", "prioritiz", "roadmap")),
    "analysis": ("Pensamiento analítico", "Analytical thinking", ("analiz", "analit", "dato", "metric", "monitor", "evalu", "analy", "data", "metric", "assess")),
    "learning": ("Aprendizaje y adaptabilidad", "Learning agility", ("aprend", "adapt", "certific", "capacit", "learn", "upskill", "autodidact", "curios")),
}
DEFAULTS = ("teamwork", "problem_solving", "results")
_TOKEN = re.compile(r"[a-z0-9+#&]+")


def label(key: str, lang: str) -> str:
    es, en, _ = COMPETENCIES[key]
    return en if lang == "en" else es


def hits(text: str, key: str) -> int:
    """Cuántos disparadores de la competencia aparecen en el texto (por raíz de palabra o frase)."""
    n = norm(text)
    toks = _TOKEN.findall(n)
    count = 0
    for trig in COMPETENCIES[key][2]:
        if " " in trig or "-" in trig:
            count += n.count(trig)
        else:
            count += sum(1 for t in toks if t.startswith(trig))
    return count


def job_competencies(text: str, limit: int = 6) -> list[tuple[str, int]]:
    """Competencias que más pide la oferta, [(clave, menciones)]. Si hay pocas, se completa con las habituales (marcadas con 0 menciones)."""
    scored = sorted(((k, hits(text, k)) for k in COMPETENCIES), key=lambda kv: -kv[1])
    out = [(k, c) for k, c in scored if c > 0][:limit]
    for d in DEFAULTS:
        if len(out) < 3 and d not in {k for k, _ in out}:
            out.append((d, 0))
    return out
