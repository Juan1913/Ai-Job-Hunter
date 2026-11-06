"""Las seis dimensiones de la evaluación. Cada una es determinista y devuelve puntaje 0-1, explicación con evidencia y fuente.
Mensajes bilingües en línea: `ctx.t(es, en)` elige según el idioma del usuario."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from app.domain.job import Job
from app.domain.profile import DealBreakers, Profile, SearchPreferences
from app.evaluation import extract as ex
from app.evaluation.fx import monthly_usd
from app.evaluation.models import WEIGHTS, Dimension, Flag, Requirements, Veto
from app.text import contains, norm

LABELS = {"skills": ("Ajuste de habilidades", "Skills fit"), "seniority": ("Seniority y años", "Seniority and years"),
          "location": ("Ubicación y modalidad", "Location and work mode"), "language": ("Idiomas", "Languages"),
          "salary": ("Salario", "Salary"), "freshness": ("Frescura y urgencia", "Freshness and urgency")}
_WORLDWIDE = re.compile(r"\b(worldwide|anywhere|global|latam|latin america|latinoamerica|toda latinoamerica|cualquier pais|any country)\b")


@dataclass
class Ctx:
    job: Job
    profile: Profile
    prefs: SearchPreferences
    db: DealBreakers
    today: date
    lang: str
    stage1: float | None = None
    match: int | None = None
    gaps: list | None = None
    req: Requirements = field(default_factory=Requirements)
    flags: list[Flag] = field(default_factory=list)
    vetoes: list[Veto] = field(default_factory=list)

    def t(self, es: str, en: str) -> str:
        return en if self.lang == "en" else es

    def dim(self, key, score, status, explanation, evidence=None, source="") -> Dimension:
        return Dimension(key=key, label=LABELS[key][1 if self.lang == "en" else 0], score=round(max(0.0, min(1.0, score)), 3), weight=WEIGHTS[key],
                         effective_weight=0.0, status=status, explanation=explanation, evidence=evidence or [], source=source)

    def neutral(self, key, explanation, source="") -> Dimension:
        return self.dim(key, 0.5, "neutral", explanation, source=source)


def _date(s: str | None) -> date | None:
    try:
        return datetime.fromisoformat(s[:10]).date() if s else None
    except ValueError:
        return None


def _status(score: float) -> str:
    return "ok" if score >= 0.6 else "warning"


# ------------------------------------------------------------------ (a) habilidades
def skills(c: Ctx) -> Dimension:
    if c.gaps:
        from app.ats.gaps import match_score
        from app.domain.tailoring import GapStatus, KeywordGap
        gaps = [KeywordGap.model_validate(g) for g in c.gaps]
        pct = c.match if c.match is not None else match_score(gaps)
        have = [g.keyword for g in gaps if g.status == GapStatus.HAVE][:4]
        adj = [g.keyword for g in gaps if g.status == GapStatus.ADJACENT][:3]
        miss = [g.keyword for g in gaps if g.status == GapStatus.MISSING and not g.confirmed][:4]
        ev = [c.t("Tienes: ", "You have: ") + ", ".join(have)] if have else []
        if adj:
            ev.append(c.t("Cercanas: ", "Adjacent: ") + ", ".join(adj))
        if miss:
            ev.append(c.t("Te faltan: ", "Missing: ") + ", ".join(miss))
        s = pct / 100
        expl = c.t(f"Cubres {pct}% de lo que pide la oferta", f"You cover {pct}% of what the posting asks") + (c.t(f"; faltan {', '.join(miss[:3])}", f"; missing {', '.join(miss[:3])}") if miss else "")
        return c.dim("skills", s, _status(s), expl, ev, c.t("análisis con LLM (guardado)", "cached LLM analysis"))
    if c.stage1 is not None:
        s = c.stage1 / 100
        return c.dim("skills", s, _status(s), c.t(f"Coincidencia aproximada con tu perfil: {c.stage1:.0f}/100 (sin análisis detallado todavía)",
                                                 f"Approximate match with your profile: {c.stage1:.0f}/100 (no detailed analysis yet)"),
                     source=c.t("etapa 1, sin LLM", "stage 1, no LLM"))
    c.flags.append(Flag(code="no_analysis", message=c.t("Aún no hay puntaje de habilidades: ejecuta el análisis", "No skills score yet: run the analysis"), severity="info"))
    return c.neutral("skills", c.t("Sin puntaje de habilidades todavía", "No skills score yet"), c.t("sin dato", "no data"))


# ------------------------------------------------------------------ (b) seniority / años
def seniority(c: Ctx) -> Dimension:
    job = c.job
    req_years, quote = (c.req.years, "") if c.req.years is not None else ex.required_years(f"{job.title}. {job.description}")
    jl = c.req.level or ex.job_level(job)
    py = ex.profile_years(c.profile, c.today)
    pl = ex.profile_level(c.profile, py)
    c.req.years, c.req.level = req_years, jl
    ev, parts = [], []
    if req_years is not None:
        ev.append(c.t(f"La oferta pide {req_years:g} años", f"The posting asks for {req_years:g} years") + (f": «{quote[:90]}»" if quote else ""))
    if jl:
        ev.append(c.t(f"Nivel de la oferta: {jl}", f"Posting level: {jl}"))
    if py is not None:
        ev.append(c.t(f"Tus años (según tus fechas): {py:g}", f"Your years (from your dates): {py:g}") + (f"; nivel {pl}" if pl else ""))
    ys = None
    if req_years is not None and py is not None:
        if py >= req_years:
            ys = 0.6 if req_years > 0 and py >= max(req_years * 2.5, req_years + 6) else 1.0
            if ys < 1:
                c.flags.append(Flag(code="overqualified", message=c.t(f"Sobrecalificado: {py:g} años frente a {req_years:g} pedidos", f"Overqualified: {py:g} years vs {req_years:g} asked"), severity="warning"))
        else:
            ys = (py / req_years) ** 1.6 if req_years else 1.0
            if py / req_years < 0.6:
                c.flags.append(Flag(code="underqualified", message=c.t(f"Te faltan {req_years - py:.1f} años de experiencia", f"You are {req_years - py:.1f} years short"), severity="warning"))
        parts.append((0.6, ys))
    ls = None
    if jl and pl:
        diff = ex.LEVELS.index(jl) - ex.LEVELS.index(pl)
        ls = {0: 1.0, 1: 0.6, 2: 0.25}.get(diff) if diff >= 0 else {-1: 0.75, -2: 0.45}.get(diff, 0.25)
        if ls is None:
            ls = 0.1
        if diff <= -2:
            c.flags.append(Flag(code="overqualified", message=c.t(f"Sobrecalificado: la oferta es nivel {jl} y tú {pl}", f"Overqualified: posting is {jl}, you are {pl}"), severity="warning"))
        if diff >= 2 and not any(f.code == "underqualified" for f in c.flags):
            c.flags.append(Flag(code="underqualified", message=c.t(f"Nivel por encima del tuyo ({jl} vs {pl})", f"Level above yours ({jl} vs {pl})"), severity="warning"))
        parts.append((0.4, ls))
    src = c.t("regex sobre la oferta + fechas de tu perfil", "regex on the posting + your profile dates") + (" + LLM" if c.req.source == "llm" else "")
    if not parts:
        return c.neutral("seniority", c.t("La oferta no indica años ni nivel, o tu perfil no tiene fechas", "The posting gives no years/level or your profile has no dates"), src)
    tw = sum(w for w, _ in parts)
    s = sum(w * v for w, v in parts) / tw
    bits = []
    if ys is not None:
        bits.append(c.t(f"{py:g} años vs {req_years:g} pedidos", f"{py:g} years vs {req_years:g} asked"))
    if ls is not None:
        bits.append(c.t(f"nivel {pl} vs {jl}", f"level {pl} vs {jl}"))
    return c.dim("seniority", s, _status(s), c.t("Seniority: ", "Seniority: ") + "; ".join(bits), ev, src)


# ------------------------------------------------------------------ (c) ubicación / modalidad
def job_mode(job: Job) -> str | None:
    return job.work_mode or ("remote" if job.remote else None)


def location(c: Ctx) -> Dimension:
    job, pr = c.job, c.prefs
    mode = job_mode(job)
    countries = {x.upper() for x in pr.countries}
    jc = (job.country or "").upper() or None
    blob = norm(f"{job.title} {job.location} {job.description[:600]}")
    ev = [x for x in (c.t("Modalidad: ", "Mode: ") + mode if mode else "", c.t("Ubicación: ", "Location: ") + job.location if job.location else "") if x]
    src = c.t("datos de la oferta vs tus preferencias", "posting data vs your preferences")
    if mode == "remote":
        if not pr.remote:
            return c.dim("location", 0.45, "warning", c.t("Es remota y marcaste que no priorizas remoto", "It is remote and you do not prioritise remote"), ev, src)
        if countries and jc and jc not in countries and not _WORLDWIDE.search(blob):
            return c.dim("location", 0.55, "warning", c.t(f"Remota, pero parece limitada a {jc}", f"Remote, but seems limited to {jc}"), ev, src)
        return c.dim("location", 1.0, "ok", c.t("Remota, como prefieres", "Remote, as you prefer"), ev, src)
    country_bad = bool(countries and jc and jc not in countries)
    regions = [norm(r) for r in pr.regions if r.strip()]
    region_ok = any(contains(norm(job.location), r) or r in norm(job.location) for r in regions) if regions else None
    home_ok = bool(c.profile.location) and bool(job.location) and norm(c.profile.location.split(",")[0]) in norm(job.location)
    label = {"hybrid": c.t("híbrida", "hybrid"), "onsite": c.t("presencial", "on-site")}.get(mode or "", c.t("de modalidad no indicada", "of unspecified mode"))
    if country_bad:
        return c.dim("location", 0.1, "warning", c.t(f"Oferta {label} en {jc}, fuera de tus países", f"{label.capitalize()} posting in {jc}, outside your countries"), ev, src)
    if not countries and not regions and not c.profile.location:
        return c.neutral("location", c.t("No has declarado dónde quieres trabajar", "You have not declared where you want to work"), src)
    if not jc and not job.location and mode != "remote":
        return c.neutral("location", c.t("La oferta no indica ubicación", "The posting gives no location"), src)
    if region_ok is False and not home_ok:
        return c.dim("location", 0.5, "warning", c.t(f"Oferta {label} fuera de tus ciudades/regiones preferidas ({job.location or jc})",
                                                     f"{label.capitalize()} posting outside your preferred cities ({job.location or jc})"), ev, src)
    base = 1.0 if (region_ok or home_ok) else 0.8
    if not mode:
        base = min(base, 0.65)
    return c.dim("location", base, "ok", c.t(f"Oferta {label} en tu zona ({job.location or jc})", f"{label.capitalize()} posting in your area ({job.location or jc})"), ev, src)


# ------------------------------------------------------------------ (d) idiomas (language gate)
def language(c: Ctx) -> Dimension:
    job = c.job
    have = ex.profile_languages(c.profile)
    for code in c.prefs.languages:
        have.setdefault(code.lower()[:2], None)
    if not have:
        return c.neutral("language", c.t("No declaraste idiomas en tu perfil", "You declared no languages in your profile"), c.t("sin dato", "no data"))
    reqs = ex.required_languages(f"{job.title}. {job.description}", job.language) if not c.req.languages else c.req.languages
    c.req.languages = reqs
    name = lambda code: ex.LANG_NAMES.get(code, (code, code))[1 if c.lang == "en" else 0]   # noqa: E731
    score, ev, msgs, status = 1.0, [], [], "ok"
    for r in reqs:
        code, need, mandatory = r["code"], r.get("level"), r.get("required", True)
        if code not in have:
            ev.append(c.t(f"La oferta pide {name(code)}; no lo declaraste", f"The posting asks for {name(code)}; you did not declare it") + (f": «{r.get('quote', '')[:80]}»" if r.get("quote") else ""))
            if mandatory and (need or 0) >= 4:
                c.vetoes.append(Veto(code="language_missing", reason=c.t(f"Exige {name(code)} de nivel alto y no lo tienes declarado", f"Requires {name(code)} at a high level and you have not declared it"), evidence=r.get("quote", "")[:120]))
                score, status = 0.0, "veto"
            elif mandatory:
                score, status = min(score, 0.3), "warning" if status != "veto" else status
                msgs.append(c.t(f"pide {name(code)} (no declarado)", f"asks for {name(code)} (not declared)"))
            else:
                score = min(score, 0.85)
                msgs.append(c.t(f"valora {name(code)} (no declarado)", f"values {name(code)} (not declared)"))
        else:
            mine = have[code]
            if mine is None:
                score = min(score, 0.85)
                ev.append(c.t(f"{name(code)}: declarado sin nivel", f"{name(code)}: declared without level"))
            elif need and mine < need:
                gap = need - mine
                score = min(score, 0.35 if gap >= 2 else 0.7)
                status = "warning" if status != "veto" else status
                msgs.append(c.t(f"{name(code)}: pide nivel {need}/6 y tienes {mine}/6", f"{name(code)}: needs level {need}/6, you have {mine}/6"))
                ev.append(msgs[-1])
    if job.language and job.language not in have and not reqs and score >= 0.35:
        score, status = min(score, 0.35), "warning"
        msgs.append(c.t(f"la oferta está escrita en {name(job.language)}, que no declaraste", f"the posting is written in {name(job.language)}, which you did not declare"))
    if status == "veto":
        expl = c.t("Veto de idioma: ", "Language veto: ") + c.vetoes[-1].reason
    elif msgs:
        expl = c.t("Idiomas: ", "Languages: ") + "; ".join(msgs)
    elif reqs:
        expl = c.t("Cumples los idiomas que pide la oferta", "You meet the languages the posting asks for")
    else:
        expl = c.t("La oferta no exige idiomas concretos", "The posting asks for no specific language")
    return c.dim("language", score, status, expl, ev, c.t("regex sobre la oferta vs idiomas de tu perfil", "regex on the posting vs your profile languages") + (" + LLM" if c.req.source == "llm" else ""))


# ------------------------------------------------------------------ (e) salario
def job_monthly_usd(job: Job) -> tuple[float | None, float | None]:
    """(min, max) mensual en USD (aprox.). None si no hay dato comparable (sin cifra, moneda o periodo desconocidos)."""
    lo, hi = job.salary_min, job.salary_max
    if lo is None and hi is None:
        return None, None
    lo = lo if lo is not None else hi
    hi = hi if hi is not None else lo
    return monthly_usd(lo, job.salary_currency, job.salary_period), monthly_usd(hi, job.salary_currency, job.salary_period)


def salary(c: Ctx) -> Dimension:
    job = c.job
    lo, hi = job_monthly_usd(job)
    src = c.t("salario publicado (convertido a USD/mes aprox.)", "published salary (converted to approx. USD/month)")
    if job.salary_min is None and job.salary_max is None:
        c.flags.append(Flag(code="no_salary", message=c.t("La oferta no publica salario", "The posting publishes no salary"), severity="info"))
        return c.neutral("salary", c.t("La oferta no publica salario: no cuenta en el puntaje", "No published salary: not counted in the score"), c.t("sin dato", "no data"))
    if lo is None or hi is None:
        c.flags.append(Flag(code="salary_unknown_currency", message=c.t("Salario con moneda o periodo no reconocidos", "Salary with unrecognised currency or period"), severity="info"))
        return c.neutral("salary", c.t(f"Salario publicado ({job.salary}), pero sin moneda/periodo comparables", f"Published salary ({job.salary}) but no comparable currency/period"), c.t("sin dato comparable", "not comparable"))
    d = c.db
    if d.min_salary is None:
        return c.neutral("salary", c.t(f"Publica ~{hi:,.0f} USD/mes; no has definido un mínimo para comparar", f"Publishes ~{hi:,.0f} USD/month; you set no minimum to compare"), src)
    mine = monthly_usd(d.min_salary, d.salary_currency, d.salary_period)
    if mine is None or mine <= 0:
        return c.neutral("salary", c.t("Tu mínimo salarial usa una moneda/periodo no reconocidos", "Your salary minimum uses an unrecognised currency/period"), src)
    ratio = hi / mine
    ev = [c.t(f"Oferta ~{lo:,.0f}-{hi:,.0f} USD/mes vs tu mínimo ~{mine:,.0f}", f"Offer ~{lo:,.0f}-{hi:,.0f} USD/month vs your minimum ~{mine:,.0f}")]
    if ratio >= 1.15:
        s, st = 1.0, "ok"
    elif ratio >= 1.0:
        s, st = 0.85, "ok"
    elif ratio >= 0.85:
        s, st = 0.45, "warning"
    else:
        s, st = 0.1, "warning"
    if ratio < 1.0 and lo < mine <= hi:
        s = max(s, 0.7)
    expl = (c.t("Salario por encima de tu mínimo", "Salary above your minimum") if ratio >= 1 else c.t(f"Salario ~{(1 - ratio) * 100:.0f}% por debajo de tu mínimo", f"Salary ~{(1 - ratio) * 100:.0f}% below your minimum"))
    return c.dim("salary", s, st, expl, ev, src)


# ------------------------------------------------------------------ (f) frescura / urgencia
def freshness(c: Ctx) -> Dimension:
    job, today = c.job, c.today
    exp, posted = _date(job.expires_at), _date(job.posted_at)
    src = c.t("fechas de la oferta (expires_at / posted_at)", "posting dates (expires_at / posted_at)")
    if job.closed or (exp and exp < today):
        c.vetoes.append(Veto(code="expired", reason=c.t("La oferta está vencida o cerrada", "The posting is expired or closed"), evidence=job.expires_at or ""))
        return c.dim("freshness", 0.0, "veto", c.t("Oferta vencida o cerrada", "Expired or closed posting"), [job.expires_at or ""], src)
    if exp:
        left = (exp - today).days
        if left <= 5:
            c.flags.append(Flag(code="urgent", message=c.t(f"Vence en {left} día(s): decide pronto", f"Closes in {left} day(s): decide soon"), severity="alert"))
        return c.dim("freshness", 1.0 if left <= 5 else 0.9, "ok", c.t(f"Vigente; vence el {exp.isoformat()} (en {left} días)", f"Open; closes on {exp.isoformat()} (in {left} days)"), [f"expires_at={exp}"], src)
    if posted:
        age = (today - posted).days
        s = 1.0 if age <= 7 else 0.8 if age <= 30 else 0.5 if age <= 60 else 0.3
        if age > 60:
            c.flags.append(Flag(code="stale", message=c.t(f"Publicada hace {age} días: puede estar casi cubierta", f"Posted {age} days ago: may be nearly filled"), severity="info"))
        return c.dim("freshness", s, _status(s), c.t(f"Publicada hace {age} días", f"Posted {age} days ago"), [f"posted_at={posted}"], src)
    return c.neutral("freshness", c.t("La oferta no trae fechas", "The posting has no dates"), c.t("sin dato", "no data"))


# ------------------------------------------------------------------ vetos «deal-breaker» del usuario
def user_vetoes(c: Ctx) -> None:
    job, d = c.job, c.db
    comp = norm(job.company)
    for x in d.excluded_companies:
        if x.strip() and (contains(comp, x) or norm(x) == comp):
            c.vetoes.append(Veto(code="excluded_company", reason=c.t(f"Empresa excluida por ti: {job.company}", f"Company you excluded: {job.company}"), evidence=x))
            break
    text = norm(f"{job.title} {job.description}")
    for x in d.excluded_keywords:
        if x.strip() and contains(text, x):
            c.vetoes.append(Veto(code="excluded_keyword", reason=c.t(f"Contiene «{x}», una palabra que excluiste", f"Contains “{x}”, a word you excluded"), evidence=x))
            break
    mode = job_mode(job)
    if mode and mode in [m.lower() for m in d.excluded_modes]:
        c.vetoes.append(Veto(code="excluded_mode", reason=c.t(f"Modalidad no aceptada: {mode}", f"Work mode you do not accept: {mode}"), evidence=mode))
    if d.min_salary is not None:
        _, hi = job_monthly_usd(job)
        mine = monthly_usd(d.min_salary, d.salary_currency, d.salary_period)
        if hi is not None and mine and hi < mine * 0.999:
            c.vetoes.append(Veto(code="salary_below_min", reason=c.t(f"El máximo publicado (~{hi:,.0f} USD/mes) está por debajo de tu mínimo (~{mine:,.0f})", f"Published maximum (~{hi:,.0f} USD/month) is below your minimum (~{mine:,.0f})"), evidence=job.salary or ""))
    jl = c.req.level or ex.job_level(job)
    if jl and (d.min_level or d.max_level):
        i = ex.LEVELS.index(jl)
        lo = ex.LEVELS.index(d.min_level) if d.min_level in ex.LEVELS else 0
        hi = ex.LEVELS.index(d.max_level) if d.max_level in ex.LEVELS else len(ex.LEVELS) - 1
        if not lo <= i <= hi:
            c.vetoes.append(Veto(code="level_out_of_range", reason=c.t(f"Nivel {jl} fuera de tu rango ({d.min_level or '-'} a {d.max_level or '-'})", f"Level {jl} outside your range ({d.min_level or '-'} to {d.max_level or '-'})"), evidence=jl))
    if d.required_languages and job.language and job.language not in [x.lower()[:2] for x in d.required_languages]:
        c.vetoes.append(Veto(code="language_required", reason=c.t(f"La oferta está en «{job.language}», fuera de tus idiomas obligatorios ({', '.join(d.required_languages)})",
                                                                   f"Posting is in “{job.language}”, outside your required languages ({', '.join(d.required_languages)})"), evidence=job.language))
