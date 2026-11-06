"""Análisis de candidaturas: por qué no avanzan y qué cambiar. Un ciclo observar -> diagnosticar -> proponer, con rastro auditable.

Honestidad ante todo: una empresa casi nunca explica un rechazo, así que cada causa es una HIPÓTESIS con su evidencia y su confianza,
nunca un veredicto. Todo sale de datos del propio usuario (evaluación, brechas, CV enviado, fechas, motivo que él registró).
Corre siempre con reglas (modo sin IA incluido); si la IA está disponible puede REDACTAR una nota a partir de esos hechos, sin añadir hechos nuevos."""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from sqlmodel import Session, select

from app.db import ApplicationRow, JobRow, TailoredRow, User, utcnow
from app.tracking import service as svc

NEGATIVE = ("rejected", "no_response")
FOLLOW_UP_AFTER = 7
SILENT_AFTER = 21
LOW_FIT = 3.0
LOW_ATS = 70
MIN_SAMPLE = 3

_REASON_HINTS = [   # frase que el usuario (o la empresa) dejó como motivo -> causa
    (re.compile(r"experiencia|senior|junior|nivel|a[ñn]os", re.I), "seniority", "Te señalaron experiencia o nivel"),
    (re.compile(r"salari|sueldo|pretensi|compensaci", re.I), "salary", "El motivo menciona salario o pretensiones"),
    (re.compile(r"ya (contrat|cubri|llen)|vacante (cerrada|cubierta)|position (filled|closed)|filled", re.I), "filled", "La vacante ya estaba cubierta"),
    (re.compile(r"ubicaci|ciudad|presencial|relocat|reubic|visa|permiso", re.I), "location", "El motivo menciona ubicación o permisos"),
    (re.compile(r"ingl[eé]s|english|idioma|language", re.I), "language", "El motivo menciona el idioma"),
    (re.compile(r"perfil|skills?|habilidad|tecnolog|stack|herramienta", re.I), "skills", "El motivo menciona el perfil o la tecnología"),
]
_CTA = {"skills": "profile", "cv": "job", "ats": "job", "targeting": "jobs", "followup": "job", "interview": "job", "process": "tracking"}


def _days(a: datetime | None, b: datetime | None) -> float | None:
    a, b = svc.aware(a), svc.aware(b)
    return None if a is None or b is None else (a - b).total_seconds() / 86400


def _missing(gaps: list | None) -> list[str]:
    return [g.get("keyword", "") for g in (gaps or []) if g.get("status") == "missing" and not g.get("confirmed") and g.get("keyword")]


def _cause(code: str, label: str, evidence: str, confidence: str) -> dict:
    return {"code": code, "label": label, "evidence": evidence, "confidence": confidence}


def diagnose(app: ApplicationRow, job: JobRow, cv: TailoredRow | None, now: datetime) -> dict:
    """Hipótesis para UNA candidatura, de la más a la menos respaldada."""
    ev = job.evaluation or {}
    score = ev.get("score")
    causes: list[dict] = []
    waited = _days(app.outcome_at or now, app.applied_at)
    interviews_done = [i for i in (app.interviews or []) if i.get("done")]

    if app.outcome_reason:                                       # lo que dijo el usuario o la empresa pesa más que cualquier inferencia
        for rx, code, label in _REASON_HINTS:
            if rx.search(app.outcome_reason):
                causes.append(_cause(code, label, f"«{app.outcome_reason[:140]}»", "high"))
                break
    if interviews_done:
        causes.append(_cause("after_interview", "Pasaste a entrevista: tu CV funcionó, el cierre se decidió después",
                             f"{len(interviews_done)} entrevista(s) realizada(s)", "high"))
    if score is not None and score < LOW_FIT:
        causes.append(_cause("weak_fit", "Postulaste con poco encaje con la oferta", f"Puntaje de encaje {score:.1f}/5 (umbral {LOW_FIT:.1f})", "medium"))
    weak = [d for d in ev.get("dimensions", []) if d.get("status") != "neutral" and d.get("score", 1) < 0.4 and d.get("key") != "freshness"]
    for d in weak[:2]:
        code = {"skills": "skills", "seniority": "seniority", "location": "location", "language": "language", "salary": "salary"}.get(d.get("key"), "weak_dimension")
        causes.append(_cause(code, f"{d.get('label', code)} por debajo de lo que pedía la oferta", str(d.get("explanation", ""))[:200], "medium"))
    miss = _missing(job.gaps)
    if miss and not any(c["code"] == "skills" for c in causes):
        causes.append(_cause("skills", "La oferta pedía cosas que tu perfil no respalda", "Faltaban: " + ", ".join(miss[:5]), "medium" if len(miss) >= 2 else "low"))
    ats = (cv.data.get("ats") if cv else None) or {}
    if cv is None:
        causes.append(_cause("no_tailored_cv", "No enviaste un CV adaptado a esta oferta", "No hay CV adaptado vinculado a la candidatura", "medium"))
    elif ats.get("score") is not None and (ats["score"] < LOW_ATS or ats.get("keyword_coverage", 1) < 0.6):
        causes.append(_cause("ats_low", "El CV enviado cubría poco las palabras clave", f"ATS {ats['score']}/100 · cobertura {round(ats.get('keyword_coverage', 0) * 100)}%", "medium"))
    seen = _days(app.applied_at, job.first_seen)
    if seen is not None and seen > 14:
        causes.append(_cause("late", "Postulaste tarde: la oferta ya llevaba semanas publicada", f"{round(seen)} días entre que la viste y postulaste", "low"))
    if app.outcome == "no_response" or (app.outcome is None and app.first_response_at is None and (waited or 0) >= SILENT_AFTER):
        causes.append(_cause("silence", "Sin respuesta: a menudo es proceso lento o vacante ya cubierta, no un «no» a tu perfil",
                             f"{round(waited or 0)} días sin respuesta", "low"))
    order = {"high": 0, "medium": 1, "low": 2}
    causes.sort(key=lambda c: order[c["confidence"]])
    return {"job_id": job.id, "title": job.data.get("title"), "company": job.data.get("company"), "outcome": app.outcome, "stage": app.stage,
            "applied_at": svc._iso(app.applied_at), "score": score, "ats": ats.get("score"), "causes": causes[:4],
            "inconclusive": not causes}


def _action(aid: str, prio: int, kind: str, title: str, detail: str, evidence: list[str], job_id: int | None = None) -> dict:
    return {"id": aid, "priority": prio, "kind": kind, "title": title, "detail": detail, "evidence": evidence, "target": _CTA.get(kind, "tracking"), "job_id": job_id}


def build(session: Session, user: User, now: datetime | None = None) -> dict:
    now = svc.aware(now) or utcnow()
    apps = list(session.exec(select(ApplicationRow).where(ApplicationRow.user_id == user.id)).all())
    jobs = {j.id: j for j in session.exec(select(JobRow).where(JobRow.user_id == user.id)).all()}
    cvs = {t.id: t for t in session.exec(select(TailoredRow).where(TailoredRow.user_id == user.id)).all()}
    sent = [a for a in apps if a.applied_at is not None and a.job_id in jobs]
    negative = [a for a in sent if a.outcome in NEGATIVE]
    positive = [a for a in sent if svc._interviewed(a) or a.outcome == "offer"]
    trace = [f"Observé {len(apps)} candidaturas: {len(sent)} enviadas, {len(negative)} sin éxito, {len(positive)} con entrevista u oferta."]

    diagnoses = [diagnose(a, jobs[a.job_id], cvs.get(a.tailored_id) if a.tailored_id else None, now) for a in negative]
    trace.append(f"Diagnostiqué {len(diagnoses)} candidaturas sin éxito con sus datos (encaje, brechas, CV enviado, fechas, motivo).")

    actions: list[dict] = []
    # 1) brechas que se repiten en los rechazos
    gap_count = Counter(k.lower() for a in negative for k in set(_missing(jobs[a.job_id].gaps)))
    for kw, n in gap_count.most_common(3):
        if n >= 2:
            actions.append(_action(f"skill:{kw}", 1, "skills", f"«{kw}» aparece en {n} de tus {len(negative)} candidaturas sin éxito",
                                   "Si ya lo has usado, añádelo a tu perfil con un ejemplo real (se incluirá en tus CV). Si no, valora ofertas que no lo exijan o planifica aprenderlo.",
                                   [f"Faltaba en {n} ofertas"]))
    # 2) encaje al postular
    scores_neg = [jobs[a.job_id].evaluation["score"] for a in negative if (jobs[a.job_id].evaluation or {}).get("score") is not None]
    scores_pos = [jobs[a.job_id].evaluation["score"] for a in positive if (jobs[a.job_id].evaluation or {}).get("score") is not None]
    low = [s for s in scores_neg if s < LOW_FIT]
    if len(scores_neg) >= MIN_SAMPLE and len(low) / len(scores_neg) >= 0.5:
        actions.append(_action("targeting:low-fit", 1, "targeting", "Estás postulando a ofertas con poco encaje",
                               f"{len(low)} de {len(scores_neg)} sin éxito tenían puntaje menor a {LOW_FIT:.1f}. Prioriza las marcadas «Vale la pena postular».", [f"Puntaje medio de tus rechazos: {sum(scores_neg) / len(scores_neg):.1f}/5"]))
    if scores_pos and scores_neg:
        actions.append(_action("targeting:threshold", 2, "targeting", "Dónde te va mejor",
                               f"Tus procesos que avanzaron tenían puntaje medio {sum(scores_pos) / len(scores_pos):.1f}/5; los que no, {sum(scores_neg) / len(scores_neg):.1f}/5.",
                               [f"{len(scores_pos)} con avance vs {len(scores_neg)} sin éxito"]))
    # 3) CV genérico o con poca cobertura
    generic = [a for a in negative if not a.tailored_id]
    if generic:
        actions.append(_action("cv:generic", 1, "cv", f"{len(generic)} candidaturas sin CV adaptado",
                               "Genera el CV adaptado a cada oferta antes de postular: sube la cobertura de palabras clave sin inventar nada.", [f"{len(generic)} de {len(negative)}"], generic[0].job_id))
    weak_ats = [d for d in diagnoses if any(c["code"] == "ats_low" for c in d["causes"])]
    if weak_ats:
        actions.append(_action("ats:low", 2, "ats", f"{len(weak_ats)} CV enviados cubrían pocas palabras clave", "Revisa la brecha de habilidades antes de generar el CV y confirma las que sí tienes.", [d["title"] or "" for d in weak_ats[:3]], weak_ats[0]["job_id"]))
    # 4) seguimiento pendiente
    stale = [a for a in sent if a.outcome is None and a.first_response_at is None and not a.interviews and (_days(now, a.applied_at) or 0) >= FOLLOW_UP_AFTER]
    if stale:
        a = sorted(stale, key=lambda a: a.applied_at)[0]
        actions.append(_action("followup", 1 if len(stale) >= 3 else 2, "followup", f"{len(stale)} candidaturas esperan respuesta hace más de {FOLLOW_UP_AFTER} días",
                               "Un mensaje breve al reclutador o a tu contacto a los 7-10 días suele reabrir procesos detenidos. Tú decides si lo envías.", [f"La más antigua: {jobs[a.job_id].data.get('title')}"], a.job_id))
    # 5) entrevistas que no cierran
    after = [d for d in diagnoses if any(c["code"] == "after_interview" for c in d["causes"])]
    if after:
        actions.append(_action("interview:close", 2, "interview", f"{len(after)} procesos llegaron a entrevista y no cerraron",
                               "El CV funcionó. Practica historias STAR y preguntas de la oferta con tu preparación de entrevista.", [d["title"] or "" for d in after[:3]], after[0]["job_id"]))
    # 6) respuesta por fuente (solo con muestra suficiente)
    summ = svc.summary(session, user, now)
    for r in summ["descriptive"]["by_source"]:
        if r["applications"] >= MIN_SAMPLE and r["interview_rate"] == 0:
            actions.append(_action(f"source:{r['key']}", 3, "targeting", f"«{r['key']}» no te ha dado entrevistas",
                                   "Con pocas muestras no es concluyente, pero puede convenir repartir tu esfuerzo en otras fuentes.", [f"{r['applications']} candidaturas, 0 entrevistas"]))
    actions.sort(key=lambda a: (a["priority"], a["id"]))
    trace.append(f"Propuse {len(actions)} acciones ordenadas por prioridad.")

    # pendientes de postular: lo que vale la pena y aún no se ha enviado
    todo = [{"job_id": a.job_id, "title": jobs[a.job_id].data.get("title"), "company": jobs[a.job_id].data.get("company"), "deadline_at": svc._iso(a.deadline_at),
             "score": (jobs[a.job_id].evaluation or {}).get("score")} for a in apps if a.applied_at is None and a.stage == "saved" and a.job_id in jobs and a.outcome is None]
    todo.sort(key=lambda t: (t["deadline_at"] is None, t["deadline_at"] or "", -(t["score"] or 0)))

    enough = len(sent) >= MIN_SAMPLE
    if not sent:
        headline = "Aún no has registrado postulaciones. Cuando marques una como enviada, empezaré a analizar qué funciona."
    elif not negative:
        headline = f"Llevas {len(sent)} postulaciones sin rechazos registrados. Marca el resultado de cada una para poder aprender de ellas."
    else:
        headline = f"{len(negative)} de {len(sent)} postulaciones no avanzaron. " + (f"Lo más repetido: {actions[0]['title'][0].lower() + actions[0]['title'][1:]}." if actions else "Aún no veo un patrón claro en tus datos.")
    return {"as_of": now.isoformat(), "mode": "rules", "headline": headline, "sample": {"sent": len(sent), "negative": len(negative), "positive": len(positive), "enough": enough},
            "actions": actions, "diagnoses": diagnoses, "to_apply": todo[:8], "trace": trace, "narrative": None,
            "caveat": "Son hipótesis a partir de tus datos: las empresas rara vez explican un rechazo. Con pocas candidaturas no son concluyentes."}


def facts_for_llm(report: dict) -> str:
    """Resumen mínimo y sin datos de contacto: lo único que la IA puede usar para redactar."""
    lines = [report["headline"]]
    lines += [f"- ACCIÓN: {a['title']}. {a['detail']}" for a in report["actions"][:5]]
    lines += [f"- {d['title']} ({d['company']}): " + "; ".join(c["label"] for c in d["causes"][:2]) for d in report["diagnoses"][:6]]
    return "\n".join(lines)
