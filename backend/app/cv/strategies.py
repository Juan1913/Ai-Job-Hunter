"""Tree-of-Thought para el CV: varias estrategias de reescritura -> evaluación determinista -> poda -> refinamiento.

    nivel 1:  k ramas, cada una con una estrategia distinta (1 llamada lenta al writer por rama)
              cada rama se evalúa SIN LLM: verificador ATS (app/ats/checker.py) + penalizaciones propias
              poda: se conservan las `beam` mejores
    nivel 2+: cada rama superviviente con problemas se REFINA (1 llamada) recibiendo su borrador y sus problemas
              se vuelve a podar a `beam`; la mejor rama de todo el árbol gana

Presupuesto de llamadas al writer: branches + (depth - 1) * beam  (en el peor caso). Con branches <= 1 `tailor_cv`
no entra aquí y se comporta como antes (un intento con reintentos del harness).

Este módulo solo orquesta y evalúa: no importa tailor.py (la generación llega como callable) para evitar ciclos.
La regla de oro se respeta en la evaluación: una rama que filtra una palabra clave no confirmada queda descalificada
frente a cualquiera que no lo haga."""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from app.ats.checker import check
from app.domain.job import Job
from app.domain.profile import Profile
from app.domain.tailoring import KeywordGap, TailoredCV, includable
from app.text import norm


@dataclass(frozen=True)
class Strategy:
    key: str
    name: str
    instruction: str


# El orden importa: con k ramas se usan las k primeras.
STRATEGIES: list[Strategy] = [
    Strategy("keywords", "denso en keywords",
             "Prioriza que cada palabra clave permitida aparezca de forma natural y exacta en titular, resumen, habilidades y viñetas. "
             "No repitas ninguna más de 3 veces ni copies frases de la oferta."),
    Strategy("achievements", "orientado a logros",
             "Reescribe las viñetas como logros: verbo de acción + qué hiciste + resultado, usando SOLO los datos y cifras que ya "
             "están en el perfil (no inventes métricas). Integra las palabras clave dentro de esos logros."),
    Strategy("conservative", "conservador",
             "Cambia lo mínimo: conserva las viñetas originales y solo ajusta el vocabulario donde una palabra clave permitida lo "
             "pida, además del titular y el resumen."),
    Strategy("pivot", "pivote de rol",
             "Reorienta el relato hacia el cargo objetivo: el resumen y el titular presentan al candidato como alguien que ya "
             "hace ese trabajo, y reordena el énfasis de cada viñeta hacia lo que más pesa en la oferta, sin cambiar los hechos."),
]

BEAM_DEFAULT = 2
STUFFING_THRESHOLD = 0.12      # fracción de 4-gramas del CV que son copia literal de la oferta
MAX_KEYWORD_REPEAT = 3
LEAK_PENALTY = 30.0


@dataclass
class Evaluation:
    score: float                                   # mayor = mejor (puede ser < 0)
    ats_score: int
    penalties: list[tuple[str, float]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)  # problemas accionables para refinar
    leaks: int = 0                                 # keywords no confirmadas filtradas (regla de oro)

    @property
    def key(self) -> tuple[bool, float]:
        return (self.leaks == 0, self.score)

    @property
    def clean(self) -> bool:
        return not self.issues          # sin nada accionable que refinar (un CV corto es solo una penalización: no se alarga inventando)


def _shingles(text: str, n: int = 4) -> set[tuple[str, ...]]:
    w = re.findall(r"[a-z0-9+#&]+", norm(text))
    return {tuple(w[i:i + n]) for i in range(len(w) - n + 1)}


def _numbers(text: str) -> set[str]:
    return {re.sub(r"\s", "", m) for m in re.findall(r"\d+(?:[.,]\d+)?\s?%|\b\d{2,}\b", text)}


def evaluate(cv: TailoredCV, profile: Profile, job: Job, gaps: list[KeywordGap]) -> Evaluation:
    """Puntaje determinista de una rama: score ATS - penalizaciones. No usa LLM."""
    rep = cv.ats = check(cv.text, gaps)
    issues = [i for i in rep.issues if not i.startswith("CV muy")]
    pen: list[tuple[str, float]] = []
    leaks = sum(1 for i in rep.issues if "no lo ha confirmado" in i)
    if leaks:
        pen.append((f"fugas de keywords no confirmadas x{leaks}", LEAK_PENALTY * leaks))

    words = len(cv.text.split())
    if words > 700:                                # un CV útil cabe en ~2 páginas
        pen.append((f"largo excesivo ({words} palabras)", min(10.0, (words - 700) / 50)))
        issues.append(f"el CV es largo ({words} palabras): condénsalo a menos de 700")
    if words < 150:
        pen.append((f"demasiado corto ({words} palabras)", 5.0))

    cv_sh = _shingles(cv.text)
    if cv_sh:
        copied = len(cv_sh & _shingles(job.description)) / len(cv_sh)
        if copied > STUFFING_THRESHOLD:
            pen.append((f"copia de la oferta {copied:.0%}", min(25.0, (copied - STUFFING_THRESHOLD) * 150)))
            issues.append("no copies frases literales de la oferta: redáctalas con tus palabras manteniendo las palabras clave")

    text_n, extra = norm(cv.text), 0.0
    for g in (g for g in gaps if includable(g)):
        n = len(re.findall(rf"(?<![a-z0-9]){re.escape(norm(g.keyword))}(?![a-z0-9])", text_n)) if norm(g.keyword) else 0
        if n > MAX_KEYWORD_REPEAT:
            extra += 2.0 * (n - MAX_KEYWORD_REPEAT)
            issues.append(f"'{g.keyword}' aparece {n} veces: déjala como máximo {MAX_KEYWORD_REPEAT}")
    if extra:
        pen.append(("keyword stuffing", min(15.0, extra)))

    known = _numbers(str(profile.model_dump(exclude={"preferences"})))
    invented = sorted(_numbers(cv.text) - known)
    if invented:
        pen.append((f"cifras no presentes en el perfil {invented}", min(30.0, 10.0 * len(invented))))
        issues.append(f"elimina estas cifras que no están en el perfil: {', '.join(invented)}")

    return Evaluation(score=rep.score - sum(p for _, p in pen), ats_score=rep.score, penalties=pen, issues=issues, leaks=leaks)


@dataclass
class Branch:
    id: str
    depth: int
    strategy: Strategy
    cv: TailoredCV
    ev: Evaluation
    parent: str | None = None
    status: str = "viva"         # viva | podada | elegida

    def line(self) -> str:
        pen = "; ".join(f"{n} -{p:.0f}" for n, p in self.ev.penalties) or "sin penalizaciones"
        par = f" (refina {self.parent})" if self.parent else ""
        return (f"[ToT] {self.id} nivel={self.depth} estrategia={self.strategy.name}{par}: ats={self.ev.ats_score} "
                f"puntaje={self.ev.score:.1f} problemas={len(self.ev.issues)} [{pen}] -> {self.status}")


@dataclass
class ToTResult:
    best: TailoredCV
    trace: list[str]
    calls: int
    branches: list[Branch]


# generate(estrategia, borrador_previo | None, problemas) -> CV. Una llamada al writer.
Generate = Callable[[Strategy, "TailoredCV | None", list[str]], TailoredCV]


def tree_of_thought(generate: Generate, profile: Profile, job: Job, gaps: list[KeywordGap],
                    branches: int = 2, depth: int = 2, beam: int = BEAM_DEFAULT,
                    strategies: list[Strategy] | None = None) -> ToTResult:
    strategies = (strategies or STRATEGIES)[:max(branches, 1)]
    beam = max(beam, 1)
    trace: list[str] = []
    all_nodes: list[Branch] = []
    calls = 0

    def spawn(bid: str, d: int, s: Strategy, prev: Branch | None) -> Branch | None:
        nonlocal calls
        calls += 1
        try:
            cv = generate(s, prev.cv if prev else None, list(prev.ev.issues) if prev else [])
            b = Branch(bid, d, s, cv, evaluate(cv, profile, job, gaps), prev.id if prev else None)
        except Exception as e:  # noqa: BLE001  (LLM caído, JSON roto...: la rama muere, el árbol sigue)
            trace.append(f"[ToT] {bid} nivel={d} estrategia={s.name}: error de generación: {e}")
            return None
        all_nodes.append(b)
        return b

    def prune(pool: list[Branch]) -> list[Branch]:
        pool = sorted(pool, key=lambda b: b.ev.key, reverse=True)
        for b in pool[beam:]:
            b.status = "podada"
        return pool[:beam]

    frontier = [b for i, s in enumerate(strategies, 1) if (b := spawn(f"d1b{i}", 1, s, None))]
    frontier = prune(frontier)
    for d in range(2, max(depth, 1) + 1):
        children = [c for b in frontier if not b.ev.clean
                    if (c := spawn(f"d{d}{b.id[2:]}", d, b.strategy, b))]
        if not children:
            break
        frontier = prune(frontier + children)

    if not all_nodes:
        raise RuntimeError("No se pudo generar el CV: " + "; ".join(trace))
    best = max(all_nodes, key=lambda b: (b.ev.key, -b.depth))
    best.status = "elegida"
    trace += [b.line() for b in all_nodes]
    trace.append(f"[ToT] elegida {best.id} ({best.strategy.name}); llamadas al writer={calls}")
    best.cv.trace = trace
    return ToTResult(best.cv, trace, calls, all_nodes)


__all__ = ["STRATEGIES", "Strategy", "Evaluation", "evaluate", "tree_of_thought", "ToTResult"]
