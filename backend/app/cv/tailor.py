import json

from app.agents.harness import run_with_guard
from app.ai.privacy import profile_for_prompt
from app.ai.report import disabled_reason, usable
from app.ats.checker import check
from app.config import settings
from app.cv.render import render_text
from app.cv.strategies import Strategy, tree_of_thought
from app.domain.job import Job
from app.domain.profile import Profile
from app.domain.tailoring import KeywordGap, TailoredCV, includable
from app.llm.client import LLMClient
from app.safety.integrity import UNTRUSTED_RULE, offer_block
from app.text import clean_title, contains, norm

SYSTEM = """Eres un redactor de CVs experto en superar filtros ATS. Adaptas el CV del candidato a una oferta.
REGLAS ESTRICTAS:
1. Usa SOLO hechos del perfil. No inventes empresas, cargos, fechas, herramientas ni logros.
2. Incluye de forma natural las "PALABRAS CLAVE PERMITIDAS", con la redacción exacta de la oferta.
   Las de tipo "adyacente" se logran reescribiendo experiencia real citada en su evidencia con el vocabulario de la oferta.
3. NO uses ninguna palabra de "PROHIBIDAS": el candidato no las tiene.
4. Mantén empresas y cargos tal cual; solo reescribe viñetas, resumen y titular. Orden de experiencia: igual que el perfil.
Devuelve SOLO JSON:
{"headline":"","summary":"","skills":[""],"experience":[{"bullets":[""]}]}
"experience" debe tener un elemento por cada experiencia del perfil, en el mismo orden."""
SYSTEM += UNTRUSTED_RULE


def _draft_json(cv: TailoredCV) -> str:
    return json.dumps({"headline": cv.headline, "summary": cv.summary, "skills": cv.skills,
                       "experience": [{"bullets": e.get("bullets", [])} for e in cv.experience]}, ensure_ascii=False)


def _prompt(profile: Profile, job: Job, gaps: list[KeywordGap], issues: list[str],
            strategy: Strategy | None = None, draft: TailoredCV | None = None) -> str:
    allowed = [f"- {g.keyword} ({g.status.value})" + (f" evidencia: {g.evidence}" if g.evidence else "") for g in gaps if includable(g)]
    banned = [g.keyword for g in gaps if not includable(g)]
    p = profile_for_prompt(profile)       # sin email/teléfono/ubicación/nombre: no hacen falta para redactar
    return (f"{offer_block(job, 3500)}\n\nPERFIL:\n{p}\n\n"
            f"PALABRAS CLAVE PERMITIDAS:\n" + "\n".join(allowed) + "\n\nPROHIBIDAS: " + (", ".join(banned) or "ninguna")
            + (f"\n\nESTRATEGIA DE REDACCIÓN ({strategy.name}): {strategy.instruction}" if strategy else "")
            + (f"\n\nBORRADOR ANTERIOR (mejóralo, no empieces de cero):\n{_draft_json(draft)}" if draft else "")
            + ("\n\nCORRIGE ESTOS PROBLEMAS DEL INTENTO ANTERIOR:\n" + "\n".join(issues) if issues else ""))


MAX_SKILLS = 25   # un listado más largo diluye lo relevante y no suma para el ATS


def _assemble(profile: Profile, job: Job, data: dict, gaps: list[KeywordGap]) -> TailoredCV:
    real_skills = {norm(s.name) for s in profile.skills}
    ok_keywords = {norm(g.keyword): g.keyword for g in gaps if includable(g)}
    skills = [s for s in data.get("skills", []) if isinstance(s, str) and (norm(s) in real_skills or norm(s) in ok_keywords)]
    # Toda palabra clave respaldada (tiene/adyacente con evidencia/confirmada) debe poder leerse en Skills.
    have = {norm(s) for s in skills}
    title = clean_title(job.title)
    skills += [k for n, k in ok_keywords.items() if n not in have and n != norm(title) and len(k.split()) <= 4]
    # Las habilidades REALES del perfil nunca se pierden por capricho del modelo (la lista del LLM solo decide el ORDEN: lo relevante primero).
    have = {norm(s) for s in skills}
    skills += [s.name for s in profile.skills if norm(s.name) not in have and len(s.name.split()) <= 4][: max(0, MAX_SKILLS - len(skills))]
    skills = skills[:MAX_SKILLS]
    new_exp = data.get("experience", [])
    exp = []
    for i, e in enumerate(profile.experience):
        bullets = new_exp[i].get("bullets") if i < len(new_exp) and isinstance(new_exp[i], dict) else None
        exp.append({"company": e.company, "title": e.title, "start": e.start, "end": e.end,
                    "bullets": [b for b in (bullets or e.bullets) if isinstance(b, str)]})
    headline = data.get("headline") or profile.headline
    if not contains(norm(headline + " " + (data.get("summary") or "")), title):
        headline = f"{headline} | {title}" if headline else title   # cargo objetivo visible para el ATS
    cv = TailoredCV(
        headline=headline, summary=data.get("summary") or profile.summary, skills=skills,
        experience=exp, education=[e.model_dump() for e in profile.education], certifications=profile.certifications,
        languages=profile.languages, contact={"full_name": profile.full_name, "email": profile.email, "phone": profile.phone, "location": profile.location})
    cv.text = render_text(cv)
    return cv


def tailor_cv(profile: Profile, job: Job, gaps: list[KeywordGap], llm: LLMClient | None, max_attempts: int = 3,
              branches: int | None = None, depth: int | None = None, no_ai_reason: str | None = None) -> TailoredCV:
    """Adapta el CV. `branches`/`depth` (por defecto settings.tot_*) activan el Tree-of-Thought; con branches <= 1
    se comporta como siempre: un intento con reintentos guiados por el verificador (harness)."""
    reason = no_ai_reason or disabled_reason(llm) or "mode_none"
    llm = usable(llm)
    if llm is None:                          # sin IA: CV extractivo (viñetas reales elegidas y ordenadas), mismas reglas de oro
        from app.ai.writer import tailor_extractive
        cv = tailor_extractive(profile, job, gaps, reason)
        cv.ats = check(cv.text, gaps)
        return cv
    branches = settings.tot_branches if branches is None else branches
    depth = settings.tot_depth if depth is None else depth
    if branches > 1:
        def gen(strategy: Strategy, draft: TailoredCV | None, issues: list[str]) -> TailoredCV:
            return _assemble(profile, job, llm.complete_json(SYSTEM, _prompt(profile, job, gaps, issues, strategy, draft)), gaps)

        return tree_of_thought(gen, profile, job, gaps, branches, depth, settings.tot_beam).best

    def generate(issues: list[str]) -> TailoredCV:
        return _assemble(profile, job, llm.complete_json(SYSTEM, _prompt(profile, job, gaps, issues)), gaps)

    def validate(cv: TailoredCV) -> list[str]:
        cv.ats = check(cv.text, gaps)
        return [i for i in cv.ats.issues if not i.startswith("CV muy")]  # el largo es advertencia, no se arregla reescribiendo

    res = run_with_guard(generate, validate, max_attempts)
    if res.value is None:
        raise RuntimeError("No se pudo generar el CV: " + "; ".join(res.trace))
    res.value.trace = res.trace
    return res.value
