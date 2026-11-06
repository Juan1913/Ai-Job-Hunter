"""Motor de evaluación: combina las seis dimensiones en un puntaje 1.0-5.0 y una recomendación apply | consider | skip.

PESOS (suman 1.0; una dimensión sin dato no pesa y su peso se reparte proporcionalmente entre las demás):
  skills 0.35 · seniority 0.20 · location 0.15 · language 0.10 · salary 0.10 · freshness 0.10
  puntaje = 1 + 4 * Σ(peso_efectivo * puntaje_dimensión)
VETOS (deal-breakers del usuario + objetivos como oferta vencida o idioma exigido ausente): fuerzan `skip` y limitan el puntaje a 2.0.
RECOMENDACIÓN: sin vetos, `apply` si puntaje >= umbral_apply (3.8 por defecto) y NINGUNA dimensión con dato queda bajo 0.4
  (un 5 en habilidades no compensa estar muy lejos del nivel; para seniority el piso es 0.65, así sobrecalificado => consider); `consider` si >= umbral_consider (3.0); si no, `skip`.
  Una oferta con riesgo de integridad alto (texto con instrucciones al modelo) nunca pasa de `consider`.
Todo es determinista y sin LLM; la única llamada opcional es refine_requirements (cacheada en el propio informe)."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

from app.domain.job import Job
from app.domain.profile import DealBreakers, Profile
from app.evaluation import dimensions as dm
from app.evaluation.models import Evaluation, Flag, Requirements
from app.safety.integrity import UNTRUSTED_RULE, offer_block

VERSION = "1"
FLOOR = 0.4          # ninguna dimensión con dato puede quedar por debajo para recomendar `apply`
FLOORS = {"seniority": 0.65}   # el nivel pesa más como filtro: sobrecalificado o muy por debajo => como mucho `consider`
VETO_CAP = 2.0


def fingerprint(job: Job, profile: Profile, stage1, match, gaps, today: date, lang: str) -> str:
    j = job.model_dump(mode="json")
    keep = {k: j.get(k) for k in ("title", "company", "location", "work_mode", "remote", "country", "language", "salary_min", "salary_max",
                                  "salary_currency", "salary_period", "closed", "expires_at", "posted_at", "integrity_risk")}
    keep["d"] = hashlib.sha1((job.description or "").encode()).hexdigest()
    p = profile.model_dump(mode="json")
    blob = json.dumps({"v": VERSION, "job": keep, "profile": {k: p[k] for k in ("skills", "experience", "languages", "location", "headline", "preferences")},
                       "s1": stage1, "m": match, "g": gaps, "day": today.isoformat(), "lang": lang}, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:20]


# Puerta de relevancia: ubicación, idioma y frescura sostenían a ofertas ajenas al perfil en "consider" (medido sobre ofertas
# reales: AUC 0.91 de la etapa 1 -> 0.82 tras evaluar). Si el ajuste de habilidades es bajo, el puntaje se hunde en vez de promediarse.
# Dos escalas porque el puntaje barato de la etapa 1 y el del análisis detallado (cobertura de requisitos) no son comparables.
GATE_STAGE1 = (0.15, 0.40)       # ajuste <=0.15 => puerta 0 (fuera de tu área); >=0.40 => puerta 1
GATE_ANALYSIS = (0.20, 0.55)
OFF_FIELD_BELOW = 0.35           # por debajo de esta puerta se avisa «fuera de tu área»


def relevance_gate(skills_dim, detailed: bool) -> float:
    if skills_dim is None or skills_dim.status == "neutral":
        return 1.0
    lo, hi = GATE_ANALYSIS if detailed else GATE_STAGE1
    return round(min(1.0, max(0.0, (skills_dim.score - lo) / (hi - lo))), 3)


def _reasons(dims, vetoes, flags, lang: str) -> list[str]:
    out: list[str] = [v.reason for v in vetoes]
    out += [f.message for f in flags if f.severity in ("alert", "warning") and f.message not in out]
    known = [d for d in dims if d.status != "neutral"]
    weak = sorted((d for d in known if d.score < 0.6 and d.explanation not in out), key=lambda d: d.score)
    strong = sorted((d for d in known if d.score >= 0.8 and d.explanation not in out), key=lambda d: -d.weight * d.score)
    if not vetoes and strong:
        out.insert(0, strong[0].explanation)          # lo mejor primero cuando no hay un veto que explicar
        strong = strong[1:]
    for d in weak + strong:
        if d.explanation not in out:
            out.append(d.explanation)
    return out[:3]


def evaluate(job: Job, profile: Profile, *, stage1: float | None = None, match: int | None = None, gaps: list | None = None,
             today: date | None = None, lang: str = "es", job_id: int | None = None, cached_req: Requirements | None = None) -> Evaluation:
    today = today or datetime.now(timezone.utc).date()
    lang = "en" if lang == "en" else "es"
    prefs = profile.preferences
    db = prefs.deal_breakers or DealBreakers()
    req = cached_req.model_copy(deep=True) if cached_req else Requirements()
    c = dm.Ctx(job=job, profile=profile, prefs=prefs, db=db, today=today, lang=lang, stage1=stage1, match=match, gaps=gaps, req=req)
    dims = [dm.skills(c), dm.seniority(c), dm.location(c), dm.language(c), dm.salary(c), dm.freshness(c)]
    dm.user_vetoes(c)
    vetoes = list({v.code: v for v in c.vetoes}.values())

    total_w = sum(d.weight for d in dims if d.status != "neutral")
    for d in dims:
        d.effective_weight = round(d.weight / total_w, 4) if total_w and d.status != "neutral" else 0.0
    weighted = sum(d.effective_weight * d.score for d in dims) if total_w else 0.5
    gate = relevance_gate(dims[0], detailed=bool(c.gaps))
    raw = round(1 + 4 * weighted * gate, 2)
    score = min(raw, VETO_CAP) if vetoes else raw

    thr_apply, thr_consider = db.apply_threshold, min(db.consider_threshold, db.apply_threshold)
    flags = list(c.flags)
    if gate < OFF_FIELD_BELOW:
        flags.append(Flag(code="off_field", severity="warning", message=(f"Parece fuera de tu área: el ajuste con tu perfil es solo {dims[0].score * 100:.0f}/100" if lang == "es"
                                                                      else f"This looks outside your field: profile fit is only {dims[0].score * 100:.0f}/100")))
    if job.integrity_risk == "high":
        flags.append(Flag(code="integrity", severity="alert", message=("La oferta traía texto con instrucciones al modelo; se eliminó antes de analizarla. Revísala con cuidado"
                                                                       if lang == "es" else "The posting contained text aimed at the model; it was removed before analysis. Review it carefully")))
    elif job.integrity_risk == "medium":
        flags.append(Flag(code="integrity", severity="warning", message=("La oferta tenía contenido oculto o sospechoso (ya neutralizado)" if lang == "es" else "The posting had hidden or suspicious content (already neutralised)")))
    if vetoes:
        rec = "skip"
    else:
        floor_ok = all(d.score >= FLOORS.get(d.key, FLOOR) for d in dims if d.status != "neutral")
        if score >= thr_apply and floor_ok and job.integrity_risk != "high":
            rec = "apply"
        elif score >= thr_consider:
            rec = "consider"
            if score >= thr_apply:
                why = (("Alguna dimensión es demasiado débil para recomendar postular" if lang == "es" else "One dimension is too weak to recommend applying") if not floor_ok
                       else ("Se limita a «considerar» por el riesgo de integridad del texto" if lang == "es" else "Capped at “consider” because of the text integrity risk"))
                flags.append(Flag(code="apply_capped", severity="info", message=why))
        else:
            rec = "skip"
    expl = ", ".join(f"{d.key} {d.weight:.0%}" for d in dims)
    neutral = [d.key for d in dims if d.status == "neutral"]
    weights_explained = (f"Pesos: {expl}. Puntaje = 1 + 4 x promedio ponderado (0-1) x puerta de relevancia (si el ajuste de habilidades es bajo, el puntaje cae)." + (f" Sin dato y fuera del cálculo: {', '.join(neutral)}." if neutral else "")
                         + f" Umbrales: apply >= {thr_apply:g}, consider >= {thr_consider:g}; con vetos el puntaje se limita a {VETO_CAP:g}." if lang == "es" else
                         f"Weights: {expl}. Score = 1 + 4 x weighted average (0-1) x relevance gate (a low skills fit drags the score down)." + (f" No data, excluded: {', '.join(neutral)}." if neutral else "")
                         + f" Thresholds: apply >= {thr_apply:g}, consider >= {thr_consider:g}; with vetoes the score is capped at {VETO_CAP:g}.")
    return Evaluation(job_id=job_id, lang=lang, score=score, raw_score=raw, recommendation=rec, thresholds={"apply": thr_apply, "consider": thr_consider},
                      dimensions=dims, vetoes=vetoes, flags=flags, reasons_top3=_reasons(dims, vetoes, flags, lang), weights_explained=weights_explained,
                      requirements=req, generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      fingerprint=fingerprint(job, profile, stage1, match, gaps, today, lang))


# ------------------------------------------------------------------ extracción opcional con LLM (cacheada)
REQ_SYS = """Extrae de una oferta de trabajo SOLO tres datos, sin inventar nada que el texto no diga:
- "years": años de experiencia exigidos (número) o null.
- "level": uno de intern, junior, mid, senior, lead, director, o null.
- "languages": idiomas que EXIGE (no el idioma en que está escrita): [{"code": "en", "level": 1-6 o null (A1=1 ... C2=6), "required": true/false}].
Devuelve SOLO JSON: {"years": null, "level": null, "languages": []}"""


def needs_llm(job: Job, req: Requirements) -> bool:
    """Solo si las regex no concluyeron nada y la descripción es lo bastante larga como para contener datos."""
    return req.years is None and req.level is None and not req.languages and len(job.description or "") >= 400


def refine_requirements(job: Job, req: Requirements, llm) -> Requirements:
    """UNA llamada al LLM ligero (texto saneado y delimitado). Si falla o responde basura, se conserva lo de las regex."""
    fp = hashlib.sha1((job.title + job.description).encode()).hexdigest()[:16]
    if req.fingerprint == fp and req.source == "llm":
        return req                                      # ya se pidió para este texto
    out = req.model_copy(deep=True)
    out.fingerprint = fp
    try:
        data = llm.complete_json(REQ_SYS + UNTRUSTED_RULE, offer_block(job, 4000))
    except Exception:   # noqa: BLE001  el LLM es opcional aquí
        return out
    if not isinstance(data, dict):
        return out
    y = data.get("years")
    if isinstance(y, (int, float)) and not isinstance(y, bool) and 0 < y <= 25:
        out.years = float(y)
    from app.evaluation.extract import LEVELS
    if data.get("level") in LEVELS:
        out.level = data["level"]
    langs = []
    for lg in data.get("languages") or []:
        if isinstance(lg, dict) and isinstance(lg.get("code"), str) and len(lg["code"]) == 2:
            lvl = lg.get("level")
            langs.append({"code": lg["code"].lower(), "level": lvl if isinstance(lvl, int) and 1 <= lvl <= 6 else None, "required": bool(lg.get("required", True)), "quote": ""})
    out.languages = langs
    out.source = "llm"
    return out
