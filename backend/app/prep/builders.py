"""Generadores deterministas de la preparación. Reglas de oro, iguales que las del CV:

- Todo lo que se afirma del candidato es TEXTO LITERAL de su perfil, con su referencia (`ref`, la misma nomenclatura del RAG).
- Lo que el perfil no tiene (el 'resultado' de una historia, una brecha) NO se rellena: se presenta como pregunta para que lo complete el usuario.
- Sin cifras de mercado inventadas: de la oferta solo se cita lo que publica; lo demás se declara 'no sabemos'.

Idioma ES/EN según la oferta. Cada función devuelve estructuras JSON listas para guardar y mostrar."""
from __future__ import annotations

import re
from collections import defaultdict

from app.cv.audit import Unit, audit_texts, experience_span_years, profile_units
from app.domain.job import Job
from app.domain.profile import Profile
from app.domain.tailoring import GapStatus, KeywordGap, includable
from app.prep.competencies import COMPETENCIES, hits, job_competencies, label
from app.text import clean_title, contains, norm

_STEM = re.compile(r"[a-z0-9+#&]{4,}")


def _stems(s: str) -> set[str]:
    return {t[:6] for t in _STEM.findall(norm(s))}


def _period(start: str | None, end: str | None, lang: str) -> str:
    if not start and not end:
        return ""
    return f"{start or '?'} - {end or ('Present' if lang == 'en' else 'Actualidad')}"


def _src(u: Unit) -> dict:
    return {"ref": u.ref, "text": u.text}


def _bullets(profile: Profile) -> list[tuple[Unit, int]]:
    return [(u, u.group or 0) for u in profile_units(profile) if u.kind == "bullet"]


def evidence_for(units: list[Unit], keyword: str, gap: KeywordGap | None = None) -> list[Unit]:
    """Líneas del perfil que respaldan una palabra clave: coincidencia exacta (viñetas primero) o, para adyacentes, la evidencia del análisis."""
    exact = [u for u in units if u.kind in ("bullet", "skill", "cert", "title", "summary", "headline") and not u.ref.startswith("summary:") and contains(u.n, keyword)]
    exact.sort(key=lambda u: {"bullet": 0, "cert": 1, "skill": 2, "title": 3}.get(u.kind, 4))
    if exact:
        return exact[:2]
    if gap and gap.evidence:
        ev = norm(gap.evidence)
        for u in units:
            if u.kind == "bullet" and (ev in u.n or u.n in ev):
                return [u]
    ks = _stems(keyword)
    if ks:
        near = [(len(ks & u.stems) / len(ks), u) for u in units if u.kind in ("bullet", "skill") and ks & u.stems]
        near.sort(key=lambda x: -x[0])
        return [u for _, u in near[:1]]
    return []


# --------------------------------------------------------------------------- (a) banco de historias STAR

def build_stories(profile: Profile, job: Job, gaps: list[KeywordGap], lang: str, max_per: int = 2) -> dict:
    """Historias STAR solo desde viñetas reales, mapeadas a las competencias de la oferta."""
    text = f"{job.title}\n{job.description}"
    comps = job_competencies(text)
    bullets = _bullets(profile)
    kws = [g.keyword for g in gaps if includable(g)]
    used: set[str] = set()
    stories: list[dict] = []
    uncovered: list[dict] = []
    es = lang != "en"
    for key, mentions in comps:
        scored = []
        for u, gi in bullets:
            h = hits(u.text, key)
            if h == 0:
                continue
            kw = sum(1 for k in kws if contains(u.n, k))
            num = 1 if re.search(r"\d", u.text) else 0
            scored.append((h * 2 + kw + 0.5 * num - 0.1 * gi, u, gi))
        scored.sort(key=lambda x: (-x[0], x[1].ref))
        picks = [x for x in scored if x[1].ref not in used][:max_per] or scored[:1]
        if not picks:
            uncovered.append({"competency": key, "label": label(key, lang), "mentions_in_offer": mentions,
                              "message": ("Ninguna viñeta de tu perfil ilustra esta competencia. Si tienes una experiencia real, agrégala a tu perfil con sus hechos; no inventes una historia."
                                          if es else "No bullet in your profile illustrates this competency. If you have a real experience, add its facts to your profile; do not invent a story.")})
            continue
        for _, u, gi in picks:
            used.add(u.ref)
            e = profile.experience[gi]
            period = _period(e.start, e.end, lang)
            has_num = bool(re.search(r"\d", u.text))
            stories.append({
                "competency": key, "label": label(key, lang), "mentions_in_offer": mentions,
                "source": {**_src(u), "company": e.company, "title": e.title, "period": period},
                "star": {
                    "situation": {"text": (f"{e.title} en {e.company}" if es else f"{e.title} at {e.company}") + (f" ({period})" if period else ""), "origin": "profile"},
                    "task": {"text": None, "origin": "user", "prompt": ("¿Cuál era el objetivo o el problema concreto? Complétalo con lo que de verdad pasó." if es
                                                                         else "What was the concrete goal or problem? Fill it in with what really happened.")},
                    "action": {"text": u.text, "origin": "profile", "ref": u.ref},
                    "result": ({"text": u.text, "origin": "profile", "ref": u.ref} if has_num else
                               {"text": None, "origin": "user", "prompt": ("¿Qué cambió gracias a lo que hiciste? Si tienes una cifra real, úsala; si no, descríbelo sin números."
                                                                           if es else "What changed because of what you did? Use a real figure if you have one; otherwise describe it without numbers.")}),
                },
                "tip": ("Cuéntala en 90 segundos: contexto en una frase, tu parte, y el resultado. Habla en primera persona de lo que TÚ hiciste."
                        if es else "Tell it in 90 seconds: context in one sentence, your part, and the outcome. Speak about what YOU did."),
            })
    # auditoría con el mismo auditor del CV: todo texto del candidato debe tener origen en el perfil
    items = [("bullet", s["star"]["action"]["text"]) for s in stories]
    for s, ln in zip(stories, audit_texts(items, profile, gaps, job, use_embeddings=False) if items else []):
        s["audit"] = {"status": ln.status, "severity": ln.severity, "source_ref": ln.source_ref}
    stories = [s for s in stories if s["audit"]["status"] != "unsupported"]
    return {"stories": stories, "uncovered": uncovered,
            "competencies": [{"key": k, "label": label(k, lang), "mentions": m} for k, m in comps]}


# --------------------------------------------------------------------------- (b) preguntas probables

def build_questions(profile: Profile, job: Job, gaps: list[KeywordGap], stories: dict, lang: str, max_technical: int = 8) -> dict:
    es = lang != "en"
    units = profile_units(profile)
    qs: list[dict] = []
    honest: list[dict] = []
    ok = [g for g in gaps if includable(g)]
    order = {GapStatus.HAVE: 0, GapStatus.ADJACENT: 1, GapStatus.MISSING: 2}
    for g in sorted(ok, key=lambda g: order[g.status])[:max_technical]:
        ev = evidence_for(units, g.keyword, g)
        qs.append({"type": "technical", "keyword": g.keyword, "status": g.status.value,
                   "question": (f"¿Cómo has usado {g.keyword} en un caso real? Cuéntame el contexto, qué hiciste tú y el resultado." if es
                                else f"How have you used {g.keyword} in a real case? Walk me through the context, what you did, and the outcome."),
                   "why": (f"La oferta pide {g.keyword}." if es else f"The job asks for {g.keyword}."), "sources": [_src(u) for u in ev],
                   "answer_with": [u.ref for u in ev]})
    for g in gaps:
        if includable(g):
            continue
        near = evidence_for(units, g.keyword, g)
        steps = ([("Dilo con claridad: «No lo he usado en un entorno real». La honestidad pesa más que un intento de disimular." if es
                   else "Say it plainly: “I haven't used it in a real setting.” Honesty beats trying to hide it.")]
                 + [(f"Conecta con lo que SÍ has hecho: «{near[0].text}»." if es else f"Connect to what you HAVE done: “{near[0].text}”.") if near else
                    ("No hay experiencia cercana en tu perfil: no fuerces una conexión." if es else "There is no close experience in your profile: don't force a connection.")]
                 + [("Explica cómo aprendes y qué harías en las primeras semanas para ponerte al día (algo concreto y realista)." if es
                     else "Explain how you learn and what you would do in the first weeks to get up to speed (concrete and realistic)."),
                    ("Si ya lo practicaste por tu cuenta, confírmalo en tu perfil antes de la entrevista; si no, no lo menciones como experiencia." if es
                     else "If you did practice it on your own, confirm it in your profile before the interview; otherwise do not present it as experience.")])
        honest.append({"type": "gap", "keyword": g.keyword,
                       "question": (f"¿Tienes experiencia con {g.keyword}?" if es else f"Do you have experience with {g.keyword}?"),
                       "why": ("La oferta lo pide y tu perfil no lo respalda." if es else "The job asks for it and your profile does not back it."),
                       "how_to_answer": steps, "adjacent_evidence": [_src(u) for u in near], "sources": [_src(u) for u in near]})
    for s in stories["stories"][:6]:
        qs.append({"type": "behavioral", "competency": s["competency"],
                   "question": (f"Cuéntame una situación en la que demostraste {s['label'].lower()}." if es else f"Tell me about a time you showed {s['label'].lower()}."),
                   "why": (f"La oferta menciona {s['label'].lower()}." if es else f"The job mentions {s['label'].lower()}."),
                   "sources": [{"ref": s["source"]["ref"], "text": s["source"]["text"]}], "answer_with": [s["source"]["ref"]]})
    seen_comp = {s["competency"] for s in stories["stories"]}
    for u in stories["uncovered"]:
        if u["competency"] not in seen_comp:
            qs.append({"type": "behavioral", "competency": u["competency"],
                       "question": (f"Cuéntame una situación en la que demostraste {u['label'].lower()}." if es else f"Tell me about a time you showed {u['label'].lower()}."),
                       "why": u["message"], "sources": [], "answer_with": []})
    if profile.experience:
        e = profile.experience[0]
        qs.insert(0, {"type": "intro", "question": ("Preséntate y cuéntame tu trayectoria." if es else "Tell me about yourself and your background."),
                      "why": ("Casi siempre es la primera pregunta." if es else "It is almost always the first question."),
                      "sources": [{"ref": "experience:0:title", "text": f"{e.title} - {e.company}"}], "answer_with": ["experience:0"]})
    company = job.company or ("la empresa" if es else "the company")
    qs.append({"type": "motivation", "question": (f"¿Por qué te interesa este puesto en {company}?" if es else f"Why are you interested in this role at {company}?"),
               "why": ("Prepárala con lo que realmente sabes de la empresa y del puesto." if es else "Prepare it with what you actually know about the company and the role."),
               "sources": []})
    return {"questions": qs, "honest_gaps": honest}


# --------------------------------------------------------------------------- (c) preguntas para el entrevistador

def build_ask(profile: Profile, job: Job, gaps: list[KeywordGap], lang: str) -> dict:
    es = lang != "en"
    out: list[dict] = []

    def add(q_es: str, q_en: str, why_es: str, why_en: str) -> None:
        out.append({"question": q_es if es else q_en, "why": why_es if es else why_en})

    add("¿Cómo se ve el éxito en este puesto a los 90 días y al año?", "What does success look like in this role at 90 days and at one year?",
        "Te dice cómo te van a medir.", "It tells you how you will be measured.")
    add("¿Cómo está formado el equipo y con quién trabajaría a diario?", "How is the team structured and who would I work with day to day?",
        "Para entender con quién y cómo trabajarías.", "To understand who you would work with and how.")
    have = [g.keyword for g in gaps if g.status != GapStatus.MISSING and includable(g)][:3]
    if have:
        add(f"¿Cómo usan hoy {', '.join(have)} y cuáles son los retos técnicos actuales?", f"How do you use {', '.join(have)} today and what are the current technical challenges?",
            f"La oferta menciona {', '.join(have)}, y tu perfil lo respalda.", f"The job mentions {', '.join(have)}, and your profile backs it.")
    miss = [g.keyword for g in gaps if not includable(g)][:2]
    if miss:
        add(f"La oferta menciona {', '.join(miss)}, que es nuevo para mí: ¿cómo es el proceso de incorporación y aprendizaje?",
            f"The job mentions {', '.join(miss)}, which is new to me: what does onboarding and learning look like?",
            "Muestra honestidad y interés por aprender, sin fingir experiencia.", "Shows honesty and eagerness to learn without faking experience.")
    mode = (job.work_mode or ("remote" if job.remote else "")).lower()
    if mode in ("remote", "hybrid"):
        add("¿Cómo organizan el trabajo remoto/híbrido: horarios, herramientas y presencialidad?", "How do you organize remote/hybrid work: hours, tooling and on-site expectations?",
            f"La oferta es {mode}.", f"The job is {mode}.")
    if not (job.salary or job.salary_min or job.salary_max):
        add("¿Cuál es el rango salarial previsto para el puesto?", "What is the expected salary range for this position?",
            "La oferta no publica salario.", "The job does not publish a salary.")
    if (job.employment_type or "").lower() in ("contract", "temporary", "internship"):
        add("¿Cuál es la duración prevista y las posibilidades de continuidad?", "What is the expected duration and the chances of continuing?",
            f"El tipo de contrato es {job.employment_type}.", f"The employment type is {job.employment_type}.")
    add("¿Cuáles son los siguientes pasos del proceso y en qué plazos?", "What are the next steps in the process and the timeline?",
        "Te permite hacer seguimiento a tiempo.", "It lets you follow up on time.")
    add("¿Qué oportunidades de crecimiento y formación hay en el equipo?", "What growth and training opportunities does the team offer?",
        "Para valorar el largo plazo.", "To weigh the long term.")
    return {"ask": out}


# --------------------------------------------------------------------------- (d) brief de negociación

def _num(x) -> float | None:
    return float(x) if isinstance(x, (int, float)) and x > 0 else None


def _fmt(x: float | None) -> str:
    return "?" if not x else f"{x:,.0f}".replace(",", ".")


def build_negotiation(profile: Profile, job: Job, gaps: list[KeywordGap], lang: str, user_min: float | None = None, user_max: float | None = None,
                      user_currency: str | None = None) -> dict:
    """Solo datos reales: salario publicado, rango del usuario y lo que NO sabemos. Ninguna cifra de mercado."""
    es = lang != "en"
    jmin, jmax = _num(job.salary_min), _num(job.salary_max)
    published = bool(job.salary or jmin or jmax)
    offer = {"published": published, "text": job.salary, "min": jmin, "max": jmax, "currency": job.salary_currency, "period": job.salary_period}
    umin, umax = _num(user_min), _num(user_max)
    user = {"min": umin, "max": umax, "currency": user_currency} if (umin or umax) else None
    known: list[str] = []
    unknown: list[str] = []
    comparison = None
    if published:
        known.append((f"La oferta publica: {job.salary}" if job.salary else f"La oferta publica un rango de {_fmt(jmin)} a {_fmt(jmax)} {job.salary_currency or ''} {job.salary_period or ''}".strip())
                     if es else (f"The job lists: {job.salary}" if job.salary else f"The job lists a range of {_fmt(jmin)} to {_fmt(jmax)} {job.salary_currency or ''} {job.salary_period or ''}".strip()))
    else:
        unknown.append("La oferta no publica salario: averigua el rango antes de dar un número." if es else "The job does not list a salary: find out the range before naming a number.")
    if user:
        known.append((f"Tu rango deseado: {_fmt(umin)} - {_fmt(umax)} {user_currency or ''}".strip()) if es else f"Your desired range: {_fmt(umin)} - {_fmt(umax)} {user_currency or ''}".strip())
        same_cur = (not user_currency) or (not job.salary_currency) or user_currency.upper() == job.salary_currency.upper()
        if (jmin or jmax) and same_cur:
            lo, hi = jmin or jmax, jmax or jmin
            ulo, uhi = umin or umax, umax or umin
            rel = "above" if ulo > hi else "below" if uhi < lo else "overlap"
            comparison = {"relation": rel, "assumes_same_currency": not (user_currency and job.salary_currency), "assumes_same_period": True,
                          "note": {"above": "Tu mínimo está por encima del máximo publicado: prepárate para argumentar o ajustar expectativas." if es else "Your minimum is above the published maximum: be ready to justify it or adjust.",
                                   "below": "Tu rango está por debajo de lo publicado: no hace falta pedir menos." if es else "Your range is below what is published: no need to ask for less.",
                                   "overlap": "Tu rango se solapa con el publicado." if es else "Your range overlaps with the published one."}[rel]}
        elif (jmin or jmax) and not same_cur:
            unknown.append("Tu rango y el de la oferta están en monedas distintas: no se comparan sin una conversión que tú confirmes." if es else "Your range and the job's are in different currencies: not compared without a conversion you confirm.")
    else:
        unknown.append("No has indicado tu rango salarial deseado: defínelo (mínimo aceptable y objetivo) antes de la entrevista." if es else "You have not set a desired salary range: define it (acceptable minimum and target) before the interview.")
    unknown += ([ "Rango de mercado de este cargo y ciudad: no tenemos datos fiables; contrástalo con fuentes públicas o con colegas.",
                  "Beneficios, bonos, equity, días libres y modalidad: pregunta y pídelo por escrito.",
                  "Cuánto margen real tiene la empresa: nadie lo sabe hasta que hay una oferta."] if es else
                ["Market range for this role and city: we have no reliable data; check public sources or peers.",
                 "Benefits, bonus, equity, time off and work mode: ask and get it in writing.",
                 "How much room the company really has: nobody knows until there is an offer."])
    units = profile_units(profile)
    strengths = []
    for g in gaps:
        if g.status == GapStatus.HAVE:
            ev = evidence_for(units, g.keyword, g)
            strengths.append({"keyword": g.keyword, "sources": [_src(u) for u in ev]})
    certs = [{"text": c, "ref": f"cert:{i}"} for i, c in enumerate(profile.certifications)]
    span = experience_span_years(profile)
    leverage = {"requirements_you_meet": strengths[:8], "certifications": certs[:5], "years_in_profile": round(span, 1) if span else None,
                "note": ("Apóyate solo en lo verificable de tu perfil; cada punto cita su origen." if es else "Lean only on what is verifiable in your profile; each point cites its source.")}
    prepare = ([ "Define tres números: mínimo aceptable, objetivo y primer planteamiento (el techo razonable).",
                 "Prepara dos o tres logros reales de tu perfil que justifiquen tu rango.",
                 "Piensa qué más te importa además del salario (modalidad, formación, horario, revisión a los 6 meses).",
                 "Decide tu alternativa si no hay acuerdo (otras candidaturas activas en tu pipeline)."] if es else
               ["Define three numbers: acceptable minimum, target and opening ask (the reasonable ceiling).",
                "Prepare two or three real achievements from your profile that justify your range.",
                "Think about what else matters besides salary (work mode, training, schedule, a six-month review).",
                "Decide your fallback if there is no deal (other active applications in your pipeline)."])
    tactics = ([ "Si te preguntan primero por tus expectativas, devuelve la pregunta por el rango del puesto; si insisten, da un rango, no un número único.",
                 "Negocia el paquete completo, no solo el salario base.",
                 "Pide la oferta por escrito y tómate un plazo razonable para responder.",
                 "Agradece, sé concreto y no menciones cifras que no puedas respaldar."] if es else
               ["If asked about expectations first, turn it back to the position's range; if pressed, give a range, not a single number.",
                "Negotiate the whole package, not only base salary.",
                "Ask for the offer in writing and take a reasonable time to answer.",
                "Be grateful and specific, and don't cite figures you can't back up."])
    return {"offer": offer, "user_range": user, "comparison": comparison, "known": known, "unknown": unknown, "leverage": leverage, "prepare": prepare,
            "tactics_general": tactics, "disclaimer": ("Consejos generales, no datos de mercado. No inventamos cifras." if es else "General advice, not market data. We don't invent figures.")}


# --------------------------------------------------------------------------- (e) plan de mejora por frecuencia en tus ofertas

_STATUS_W = {"applied": 1.5, "interview": 1.6, "offer": 1.6, "saved": 1.2, "new": 1.0}


def build_upskill(profile: Profile, jobs: list[tuple[int, dict, str, int | None, list[dict]]], lang: str, limit: int = 15) -> dict:
    """Plan de mejora con prioridad según la frecuencia de cada brecha en las ofertas guardadas/analizadas del usuario.
    `jobs` = [(job_id, job_data, status, match, gaps)] de ofertas ya analizadas y no descartadas."""
    es = lang != "en"
    units = profile_units(profile)
    freq: dict[str, dict] = {}
    for jid, jd, status, match, gaps in jobs:
        w = _STATUS_W.get(status, 1.0) * (0.5 + (match if match is not None else 50) / 100)
        for g in gaps:
            st, kw = g.get("status"), (g.get("keyword") or "").strip()
            if not kw or st == "have" or (st == "missing" and g.get("confirmed")):
                continue
            k = norm(kw)
            d = freq.setdefault(k, {"keyword": kw, "jobs": [], "w": 0.0, "kinds": defaultdict(int), "evidence": g.get("evidence")})
            if jid in {j["id"] for j in d["jobs"]}:
                continue
            d["jobs"].append({"id": jid, "title": jd.get("title"), "company": jd.get("company"), "status": status})
            d["w"] += w
            d["kinds"][st] += 1
            d["evidence"] = d["evidence"] or g.get("evidence")
    n = len(jobs)
    items = []
    for d in freq.values():
        kind = "learn" if d["kinds"].get("missing", 0) >= d["kinds"].get("adjacent", 0) else "strengthen"
        ev = [u for u in evidence_for(units, d["keyword"], KeywordGap(keyword=d["keyword"], status=GapStatus.ADJACENT, evidence=d["evidence"])) ] if kind == "strengthen" else []
        steps = ([f"Mira cómo piden «{d['keyword']}» en estas ofertas y fija el nivel que necesitas (usarlo vs. dominarlo).",
                  "Haz un mini-proyecto o ejercicio pequeño que puedas demostrar (repositorio, informe, laboratorio) y guarda la evidencia.",
                  "Cuando puedas demostrarlo, confírmalo en tu perfil: solo entonces entrará a tus CVs. Antes, no lo presentes como experiencia."] if kind == "learn" else
                 [f"Ya tienes experiencia cercana a «{d['keyword']}»: reescribe o añade en tu perfil una viñeta real con ese detalle (qué hiciste y con qué).",
                  "Si falta la herramienta concreta, haz un ejercicio corto para cubrir la diferencia y confírmala cuando puedas demostrarla."]) if es else (
                [f"See how these jobs ask for “{d['keyword']}” and set the level you need (use it vs. master it).",
                 "Build a small project or exercise you can demonstrate (repo, report, lab) and keep the evidence.",
                 "Once you can demonstrate it, confirm it in your profile: only then will it enter your CVs. Until then, don't present it as experience."] if kind == "learn" else
                [f"You already have experience close to “{d['keyword']}”: rewrite or add a real bullet in your profile with that detail (what you did and with what).",
                 "If the specific tool is missing, do a short exercise to close the difference and confirm it when you can demonstrate it."])
        items.append({"keyword": d["keyword"], "kind": kind, "jobs_count": len(d["jobs"]), "share_of_jobs": round(len(d["jobs"]) / n, 2) if n else 0,
                      "priority": round(d["w"], 2), "example_jobs": d["jobs"][:3], "profile_evidence": [_src(u) for u in ev], "steps": steps})
    items.sort(key=lambda x: (-x["priority"], -x["jobs_count"], x["keyword"]))
    notes = []
    if n < 3:
        notes.append("Pocas ofertas analizadas: la prioridad es orientativa." if es else "Few analyzed jobs: the priority is only indicative.")
    notes.append("Prioridad = frecuencia en tus ofertas, ponderada por tu avance en cada una y su afinidad. No es una predicción de contratación." if es else
                 "Priority = frequency across your jobs, weighted by your progress in each and its fit. It is not a hiring prediction.")
    return {"based_on_jobs": n, "items": items[:limit], "notes": notes}


# --------------------------------------------------------------------------- opcional: enriquecimiento con LLM

ENRICH_SYS = """Eres un entrevistador técnico. Dada la oferta, propone hasta 5 preguntas de entrevista probables y específicas para ESTE puesto.
Reglas: solo preguntas (terminan en '?'), sin respuestas, sin cifras que no estén en la oferta, sin suponer nada sobre el candidato.
Devuelve SOLO JSON: {"questions": [{"question": "", "why": ""}]}"""


def enrich_with_llm(job: Job, llm, lang: str) -> list[dict]:
    """Preguntas adicionales del modelo (lento en CPU, opcional). Se validan: son preguntas, cortas y sin cifras ajenas a la oferta."""
    try:
        data = llm.complete_json(ENRICH_SYS, f"Idioma de salida: {'inglés' if lang == 'en' else 'español'}\nCargo: {job.title}\n\n{job.description[:3500]}")
    except Exception:  # noqa: BLE001
        return []
    out = []
    nums_ok = set(re.findall(r"\d+", job.description + job.title))
    for q in (data.get("questions", []) if isinstance(data, dict) else [])[:5]:
        if not isinstance(q, dict) or not isinstance(q.get("question"), str):
            continue
        t = q["question"].strip()
        if not t.endswith("?") or len(t) > 220 or set(re.findall(r"\d+", t)) - nums_ok:
            continue
        out.append({"type": "technical", "origin": "llm", "question": t, "why": str(q.get("why", ""))[:200], "sources": []})
    return out


def lang_for(job: Job, forced: str | None = None) -> str:
    from app.cv.lang import detect_lang
    if forced in ("es", "en"):
        return forced
    if job.language in ("es", "en"):
        return job.language
    return detect_lang(f"{clean_title(job.title)}. {job.description[:3000]}", "es")


__all__ = ["COMPETENCIES", "build_ask", "build_negotiation", "build_questions", "build_stories", "build_upskill", "enrich_with_llm", "evidence_for", "lang_for"]
