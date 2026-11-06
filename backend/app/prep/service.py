"""Orquesta la preparación: construye cada parte, la cachea por huella (perfil + oferta + brechas) y la guarda en PrepRow.
Se ejecuta como tarea en segundo plano (app/services/handlers/prep.py): la parte determinista es rápida; el LLM solo entra si el usuario lo pide."""
from __future__ import annotations

import hashlib
import json

from sqlmodel import Session, col, select

from app.ai.report import disabled_reason, usable
from app.db import ApplicationRow, JobRow, PrepRow, User, utcnow
from app.domain.job import Job
from app.domain.profile import Profile
from app.domain.tailoring import KeywordGap
from app.prep import builders as b
from app.rag.tree import profile_hash

KINDS = ("stories", "questions", "ask", "negotiation", "upskill")


def _h(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:32]


def analyzed_jobs(session: Session, user: User) -> list[tuple[int, dict, str, int | None, list[dict]]]:
    rows = session.exec(select(JobRow).where(JobRow.user_id == user.id, JobRow.status != "discarded", col(JobRow.gaps).is_not(None))).all()
    return [(r.id, r.data, r.status, r.match, r.gaps or []) for r in rows]


def input_hashes(profile: Profile, job_row: JobRow, gaps: list[dict], lang: str, jobs: list, salary: dict, use_llm: bool) -> dict[str, str]:
    base = _h(profile_hash(profile), job_row.data, gaps, lang)
    return {"stories": base, "questions": _h(base, use_llm), "ask": base, "negotiation": _h(base, salary),
            "upskill": _h(profile_hash(profile), lang, [(j[0], j[2], j[3], j[4]) for j in jobs])}


def get_cached(session: Session, user: User, job_id: int) -> dict[str, PrepRow]:
    rows = session.exec(select(PrepRow).where(PrepRow.user_id == user.id, PrepRow.job_id == job_id)).all()
    return {r.kind: r for r in rows}


def run(session: Session, user: User, job_row: JobRow, profile: Profile, gaps: list[KeywordGap], *, lang: str | None = None, parts: list[str] | None = None,
        force: bool = False, use_llm: bool = False, llm=None, salary: dict | None = None) -> dict:
    """Devuelve {kind: {"data":..., "cached": bool}}. Solo recalcula lo que cambió (perfil, oferta, brechas, rango salarial)."""
    job = Job.model_validate(job_row.data)
    no_ai_reason = disabled_reason(llm) if (use_llm and llm is not None) else None
    use_llm = use_llm and usable(llm) is not None          # sin IA (modo none, sin consentimiento, presupuesto): solo las partes deterministas
    lang = b.lang_for(job, lang)
    wanted = [k for k in (parts or KINDS) if k in KINDS]
    salary = salary or {}
    jobs = analyzed_jobs(session, user)
    gap_dicts = [g.model_dump(mode="json") for g in gaps]
    hashes = input_hashes(profile, job_row, gap_dicts, lang, jobs, salary, use_llm)
    cached = get_cached(session, user, job_row.id)
    out: dict[str, dict] = {}
    stories: dict | None = None

    def need(kind: str) -> bool:
        r = cached.get(kind)
        return force or r is None or r.input_hash != hashes[kind] or r.lang != lang

    def story_data() -> dict:
        nonlocal stories
        if stories is None:
            r = cached.get("stories")
            stories = r.data if (r is not None and not need("stories")) else b.build_stories(profile, job, gaps, lang)
        return stories

    for kind in wanted:
        if not need(kind):
            out[kind] = {"data": cached[kind].data, "cached": True}
            continue
        if kind == "stories":
            data = story_data()
        elif kind == "questions":
            data = b.build_questions(profile, job, gaps, story_data(), lang)
            if use_llm:
                try:
                    data["questions"] += b.enrich_with_llm(job, llm, lang)
                except Exception:  # noqa: BLE001  el LLM es un extra: las preguntas deterministas ya están
                    no_ai_reason = "llm_unavailable"
            data["ai_used"] = any(q.get("origin") == "llm" for q in data["questions"])
            data["ai_reason"] = None if data["ai_used"] else (no_ai_reason or "not_needed")
        elif kind == "ask":
            data = b.build_ask(profile, job, gaps, lang)
            data["ai_used"], data["ai_reason"] = False, "deterministic"
        elif kind == "negotiation":
            data = b.build_negotiation(profile, job, gaps, lang, salary.get("min"), salary.get("max"), salary.get("currency"))
        else:
            data = b.build_upskill(profile, jobs, lang)
        data.setdefault("ai_used", False)                # historias, negociación y plan de mejora son deterministas por diseño
        data.setdefault("ai_reason", "deterministic")
        row = cached.get(kind) or PrepRow(user_id=user.id, job_id=job_row.id, kind=kind)
        row.lang, row.input_hash, row.data, row.created_at = lang, hashes[kind], data, utcnow()
        session.add(row)
        out[kind] = {"data": data, "cached": False}
    session.commit()
    return out


def salary_of(session: Session, user: User, job_id: int, params: dict) -> dict:
    """Rango del usuario: lo enviado en la petición, o el guardado en su candidatura."""
    if params.get("salary_min") or params.get("salary_max"):
        return {"min": params.get("salary_min"), "max": params.get("salary_max"), "currency": params.get("salary_currency")}
    app = session.exec(select(ApplicationRow).where(ApplicationRow.user_id == user.id, ApplicationRow.job_id == job_id)).first()
    if app and (app.expected_salary_min or app.expected_salary_max):
        return {"min": app.expected_salary_min, "max": app.expected_salary_max, "currency": app.expected_salary_currency}
    return {}
