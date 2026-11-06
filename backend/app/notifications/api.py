"""Avisos en-app, preferencias/automatización y canales de salida: /api/v1/me/notifications/*."""
import json
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, col, func, select

from app.api.deps import current_user
from app.config import settings
from app.db import (
    Notification,
    NotificationChannel,
    NotificationDelivery,
    User,
    engine,
    get_session,
    utcnow,
)
from app.notifications import channels, secrets
from app.notifications import service as svc
from app.scheduling import windows

router = APIRouter(prefix="/me/notifications", tags=["notifications"])


# ------------------------------------------------------------------ avisos
@router.get("")
def list_notifications(unread: bool = False, after_id: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                       wait: float = Query(0, ge=0, le=30, description="long-poll: espera hasta N s a que haya avisos con id > after_id"),
                       user: User = Depends(current_user), s: Session = Depends(get_session)):
    rows = svc.list_for(s, user.id, unread=unread, after_id=after_id, limit=limit)
    if not rows and wait and after_id:
        deadline = time.monotonic() + wait
        while not rows and time.monotonic() < deadline:
            time.sleep(1.0)
            s.expire_all()
            rows = svc.list_for(s, user.id, unread=unread, after_id=after_id, limit=limit)
    return {"unread": _unread(s, user.id), "items": [svc.out(n) for n in rows]}


def _unread(s: Session, uid: int) -> int:
    return s.exec(select(func.count()).select_from(Notification).where(Notification.user_id == uid, col(Notification.read_at) == None)).one()   # noqa: E711


@router.get("/unread-count")
def unread_count(user: User = Depends(current_user), s: Session = Depends(get_session)):
    return {"unread": _unread(s, user.id)}


@router.post("/read-all")
def read_all(user: User = Depends(current_user), s: Session = Depends(get_session)):
    now, n = utcnow(), 0
    for x in s.exec(select(Notification).where(Notification.user_id == user.id, col(Notification.read_at) == None)).all():   # noqa: E711
        x.read_at, n = now, n + 1
        s.add(x)
    s.commit()
    return {"marked": n}


@router.get("/stream")
def stream(last_event_id: int = Query(0, ge=0, alias="after_id"), header_last_id: str | None = Header(None, alias="Last-Event-ID"),
           user: User = Depends(current_user)):
    """SSE: empuja los avisos nuevos en vivo (`event: notification`, `id:` = id del aviso). Reconecta con `Last-Event-ID` (o `?after_id=`).
    La conexión dura como máximo `JH_NOTIFY_STREAM_MAX_S`; el cliente (EventSource/fetch) reconecta solo. Lleva `Authorization: Bearer`."""
    uid = user.id
    last = last_event_id or (int(header_last_id) if header_last_id and header_last_id.isdigit() else 0)

    def gen():
        nonlocal last
        end = time.monotonic() + settings.notify_stream_max_s
        beat = 0.0
        if not last:                      # primera conexión: parte del último aviso existente (el historial se pide con GET /me/notifications)
            with Session(engine) as s:
                last = s.exec(select(func.max(Notification.id)).where(Notification.user_id == uid)).one() or 0
        yield "retry: 3000\n\n"
        while time.monotonic() < end:
            with Session(engine) as s:
                rows = svc.list_for(s, uid, after_id=last, limit=100)
                payload = [(n.id, json.dumps(svc.out(n), ensure_ascii=False)) for n in rows]
            for nid, data in payload:
                last = nid
                yield f"id: {nid}\nevent: notification\ndata: {data}\n\n"
            if not payload and time.monotonic() - beat > 15:
                beat = time.monotonic()
                yield ": keep-alive\n\n"
            time.sleep(1.0)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{nid}/read")
def mark_read(nid: int, user: User = Depends(current_user), s: Session = Depends(get_session)):
    n = s.get(Notification, nid)
    if not n or n.user_id != user.id:
        raise HTTPException(404, "Aviso no encontrado")
    if n.read_at is None:
        n.read_at = utcnow()
        s.add(n)
        s.commit()
    return svc.out(n)


# ------------------------------------------------------------------ preferencias / política de automatización
class PrefsIn(BaseModel):
    auto_evaluate: bool | None = None
    apply_to_manual: bool | None = None
    auto_tailor: bool | None = None
    tailor_daily_budget: int | None = Field(None, ge=0, le=50)
    min_score: float | None = Field(None, ge=1, le=5)
    timezone: str | None = None
    quiet_start: str | None = None
    quiet_end: str | None = None
    digest: str | None = Field(None, pattern="^(off|daily|weekly)$")
    digest_hour: int | None = Field(None, ge=0, le=23)
    digest_weekday: int | None = Field(None, ge=0, le=6)


@router.get("/preferences")
def get_preferences(user: User = Depends(current_user), s: Session = Depends(get_session)):
    from app.scheduling.automation import tailor_possible
    return {**svc.prefs_out(svc.get_prefs(s, user.id)), "tailor_available": tailor_possible()}


@router.put("/preferences")
def set_preferences(body: PrefsIn, user: User = Depends(current_user), s: Session = Depends(get_session)):
    p = svc.get_prefs(s, user.id)
    data = body.model_dump(exclude_unset=True)
    if "timezone" in data and data["timezone"] and not windows.valid_tz(data["timezone"]):
        raise HTTPException(422, f"zona horaria desconocida: {data['timezone']}")
    for k in ("quiet_start", "quiet_end"):
        if data.get(k):
            try:
                windows.parse_hhmm(data[k])
            except ValueError as e:
                raise HTTPException(422, str(e))
    q = {**{"quiet_start": p.quiet_start, "quiet_end": p.quiet_end}, **{k: data[k] for k in ("quiet_start", "quiet_end") if k in data}}
    if bool(q["quiet_start"]) != bool(q["quiet_end"]):
        raise HTTPException(422, "El silencio nocturno necesita inicio y fin (o ninguno)")
    for k, v in data.items():
        if v is not None or k in ("quiet_start", "quiet_end"):
            setattr(p, k, v)
    p.updated_at = utcnow()
    s.add(p)
    s.commit()
    s.refresh(p)
    return svc.prefs_out(p)


# ------------------------------------------------------------------ canales de salida
class ChannelIn(BaseModel):
    kind: str = Field(pattern="^(webhook|ntfy|telegram|email)$")
    label: str = Field("", max_length=60)
    url: str | None = None              # webhook
    server: str | None = None           # ntfy (por defecto https://ntfy.sh)
    topic: str | None = None            # ntfy
    chat_id: str | None = None          # telegram
    secret: str | None = Field(None, max_length=256, description="webhook: clave HMAC; telegram: token del bot; ntfy: token (opcional). Se guarda cifrado y no se devuelve.")
    enabled: bool = True


class ChannelPatch(BaseModel):
    label: str | None = Field(None, max_length=60)
    url: str | None = None
    server: str | None = None
    topic: str | None = None
    chat_id: str | None = None
    secret: str | None = Field(None, max_length=256)
    enabled: bool | None = None


def _chan_out(c: NotificationChannel) -> dict:
    """Nunca devuelve el secreto: solo si existe."""
    return {"id": c.id, "kind": c.kind, "label": c.label, "config": c.config, "has_secret": bool(c.secret_enc), "enabled": c.enabled,
            "failures": c.failures, "last_ok_at": windows.aware(c.last_ok_at).isoformat() if c.last_ok_at else None, "last_error": c.last_error,
            "created_at": windows.aware(c.created_at).isoformat()}


def _own_chan(s: Session, user: User, cid: int) -> NotificationChannel:
    c = s.get(NotificationChannel, cid)
    if not c or c.user_id != user.id:
        raise HTTPException(404, "Canal no encontrado")
    return c


@router.get("/channels")
def list_channels(user: User = Depends(current_user), s: Session = Depends(get_session)):
    return {"available": {"webhook": True, "ntfy": True, "telegram": True, "email": bool(settings.smtp_host and settings.smtp_from)},
            "items": [_chan_out(c) for c in s.exec(select(NotificationChannel).where(NotificationChannel.user_id == user.id).order_by(col(NotificationChannel.id))).all()]}


@router.post("/channels", status_code=201)
def create_channel(body: ChannelIn, user: User = Depends(current_user), s: Session = Depends(get_session)):
    n = s.exec(select(func.count()).select_from(NotificationChannel).where(NotificationChannel.user_id == user.id)).one()
    if n >= 10:
        raise HTTPException(409, "Máximo 10 canales")
    cfg_in = {k: v for k, v in body.model_dump().items() if k in ("url", "server", "topic", "chat_id") and v}
    try:
        cfg = channels.validate_config(body.kind, cfg_in, user.email)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if channels.needs_secret(body.kind) and not body.secret and body.kind == "telegram":
        raise HTTPException(422, "Telegram necesita el token de tu bot en `secret`")
    c = NotificationChannel(user_id=user.id, kind=body.kind, label=body.label or body.kind, config=cfg, enabled=body.enabled,
                            secret_enc=secrets.encrypt(body.secret) if body.secret else None)
    s.add(c)
    s.commit()
    s.refresh(c)
    return _chan_out(c)


@router.patch("/channels/{cid}")
def patch_channel(cid: int, body: ChannelPatch, user: User = Depends(current_user), s: Session = Depends(get_session)):
    c = _own_chan(s, user, cid)
    data = body.model_dump(exclude_unset=True)
    cfg_in = {**c.config, **{k: v for k, v in data.items() if k in ("url", "server", "topic", "chat_id") and v}}
    try:
        c.config = channels.validate_config(c.kind, cfg_in, user.email)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if "label" in data and data["label"] is not None:
        c.label = data["label"]
    if data.get("secret"):
        c.secret_enc = secrets.encrypt(data["secret"])
    if data.get("enabled") is not None:
        c.enabled = data["enabled"]
        if c.enabled:
            c.failures = 0
    s.add(c)
    s.commit()
    s.refresh(c)
    return _chan_out(c)


@router.delete("/channels/{cid}", status_code=204)
def delete_channel(cid: int, user: User = Depends(current_user), s: Session = Depends(get_session)):
    c = _own_chan(s, user, cid)
    for d in s.exec(select(NotificationDelivery).where(NotificationDelivery.channel_id == c.id)).all():
        s.delete(d)
    s.delete(c)
    s.commit()
    return Response(status_code=204)


@router.post("/channels/{cid}/test", status_code=202)
def test_channel(cid: int, user: User = Depends(current_user), s: Session = Depends(get_session)):
    """Envía un aviso de prueba SOLO por este canal (lo entrega el trabajador, con sus reintentos y registro)."""
    from app.worker import queue
    c = _own_chan(s, user, cid)
    now = utcnow()
    n = svc.create(s, user.id, "test", "Aviso de prueba de JobHunter", "Si lees esto, el canal funciona.", data={"channel_id": c.id}, now=now, deliver=False)
    d = NotificationDelivery(user_id=user.id, notification_id=n.id, channel_id=c.id)
    s.add(d)
    s.flush()
    queue.enqueue(s, "deliver", ref_id=d.id, user_id=user.id, payload={"delivery_id": d.id}, priority=8, max_attempts=settings.notify_max_attempts,
                  dedup_key=f"deliver:{d.id}", now=now, commit=False)
    s.commit()
    return {"notification_id": n.id, "delivery_id": d.id}


@router.get("/deliveries")
def deliveries(limit: int = Query(50, ge=1, le=200), user: User = Depends(current_user), s: Session = Depends(get_session)):
    """Registro de entregas por canal (estado, intentos, último error)."""
    rows = s.exec(select(NotificationDelivery).where(NotificationDelivery.user_id == user.id).order_by(col(NotificationDelivery.id).desc()).limit(limit)).all()
    return [{"id": d.id, "notification_id": d.notification_id, "channel_id": d.channel_id, "status": d.status, "attempts": d.attempts,
             "response_code": d.response_code, "last_error": d.last_error,
             "delivered_at": windows.aware(d.delivered_at).isoformat() if d.delivered_at else None} for d in rows]
