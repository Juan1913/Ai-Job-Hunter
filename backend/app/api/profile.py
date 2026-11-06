from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlmodel import Session

from app.ai.report import AiRun, llm_enabled
from app.api.deps import current_user, get_llm, load_profile
from app.config import settings
from app.db import ProfileRow, User, get_session, utcnow
from app.domain.profile import Profile, SearchPreferences, Skill, SkillSource
from app.llm.client import LLMClient, LLMError
from app.profile.builder import build_profile_with_report
from app.profile.extract import ExtractionError, extract_document
from app.profile.report import BuildResult

router = APIRouter(prefix="/me", tags=["profile"])


def _save(session: Session, user: User, profile: Profile) -> None:
    row = session.get(ProfileRow, user.id) or ProfileRow(user_id=user.id)
    row.data, row.updated_at = profile.model_dump(mode="json"), utcnow()
    session.add(row)
    session.commit()


async def _read_cv(file: UploadFile, llm: LLMClient) -> tuple[BuildResult, dict]:
    data = await file.read(settings.max_cv_bytes + 1)
    if len(data) > settings.max_cv_bytes:
        raise HTTPException(413, f"El CV supera {settings.max_cv_bytes // 1024 // 1024} MB")
    try:
        ex = extract_document(file.filename or "", data)
    except ValueError as e:
        raise HTTPException(415, str(e))
    except ExtractionError as e:
        raise HTTPException(422, str(e))
    except Exception:
        raise HTTPException(422, "No se pudo leer el archivo (¿está dañado o protegido con contraseña?)")
    if len(ex.text) < 50:
        raise HTTPException(422, " ".join(ex.warnings) or "No se pudo leer texto del CV (¿es un escaneo/imagen?)")
    run = AiRun(llm)
    use_llm = llm_enabled(llm)
    try:
        res = build_profile_with_report(ex.text, llm, ex.method, use_llm=True)    # sin IA el builder usa solo reglas y lo avisa
    except LLMError:                           # el builder ya degrada solo; esto es la última red de seguridad
        run.fell_back("llm_unavailable")
        res = build_profile_with_report(ex.text, None, ex.method, use_llm=False)
    res.report.warnings = [*ex.warnings, *res.report.warnings]
    ai = run.report()
    if use_llm and not res.report.llm_used:      # las reglas bastaron: no hizo falta llamar al modelo
        ai = {**ai, "ai_used": False, "ai_reason": "not_needed", "ai_message": None}
    elif use_llm and res.report.llm_failures and not res.report.llm_calls - res.report.llm_failures > 0:
        ai = {**ai, "ai_used": False, "ai_reason": "llm_unavailable"}
    return res, ai


@router.post("/cv/preview")
async def preview_cv(file: UploadFile = File(...), user: User = Depends(current_user), llm: LLMClient = Depends(get_llm)):
    """Extrae el perfil y su informe de calidad por campo SIN guardar: el panel muestra qué revisar antes de confirmar."""
    res, ai = await _read_cv(file, llm)
    return {**res.model_dump(mode="json"), "ai": ai}


@router.post("/cv", response_model=Profile)
async def upload_cv(response: Response, file: UploadFile = File(...), user: User = Depends(current_user),
                    session: Session = Depends(get_session), llm: LLMClient = Depends(get_llm)):
    res, ai = await _read_cv(file, llm)
    profile = res.profile
    response.headers["X-AI-Used"] = str(ai["ai_used"]).lower()
    if ai["ai_reason"]:
        response.headers["X-AI-Reason"] = ai["ai_reason"]
    response.headers["X-Profile-Quality"] = str(res.report.overall)
    response.headers["X-Profile-Review"] = ",".join(res.report.needs_review)
    old = session.get(ProfileRow, user.id)
    if old and old.data:                       # al re-subir se conservan preferencias y habilidades confirmadas
        prev = Profile.model_validate(old.data)
        profile.preferences = prev.preferences
        have = profile.skill_names()
        profile.skills += [s for s in prev.skills if s.source == SkillSource.USER_CONFIRMED and s.name.lower() not in have]
    _save(session, user, profile)
    return profile


@router.get("/profile", response_model=Profile)
def get_profile(user: User = Depends(current_user), session: Session = Depends(get_session)):
    return load_profile(session, user)


@router.put("/profile", response_model=Profile)
def replace_profile(profile: Profile, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """El usuario corrige lo que el modelo extrajo mal."""
    _save(session, user, profile)
    return profile


@router.put("/preferences", response_model=Profile)
def set_preferences(prefs: SearchPreferences, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Fusiona: solo cambian los campos que el cliente envía. Omitir `deal_breakers` NO los borra."""
    profile = load_profile(session, user)
    profile.preferences = profile.preferences.model_copy(update={k: getattr(prefs, k) for k in prefs.model_fields_set})
    _save(session, user, profile)
    return profile


class SkillIn(BaseModel):
    name: str


@router.post("/skills/confirm", response_model=Profile)
def confirm_skill(body: SkillIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    profile = load_profile(session, user)
    name = body.name.strip()
    if name and name.lower() not in profile.skill_names():
        profile.skills.append(Skill(name=name, source=SkillSource.USER_CONFIRMED))
        _save(session, user, profile)
    return profile


@router.delete("/skills/{name}", response_model=Profile)
def remove_skill(name: str, user: User = Depends(current_user), session: Session = Depends(get_session)):
    profile = load_profile(session, user)
    profile.skills = [s for s in profile.skills if s.name.lower() != name.lower()]
    _save(session, user, profile)
    return profile
