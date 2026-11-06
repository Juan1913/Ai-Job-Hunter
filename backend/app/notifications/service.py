"""Avisos: crear (sin duplicar), decidir qué sale por los canales y cuándo (silencio nocturno, resumen, límite por hora) y entregar.

Reglas:
  - El aviso EN-APP siempre se crea al instante: es pasivo (el panel lo muestra y el SSE lo empuja).
  - El silencio nocturno solo retrasa los canales de SALIDA (webhook/ntfy/Telegram/email): se envían al terminar el silencio.
  - Con resumen (digest) diario/semanal no hay un aviso por búsqueda: las ofertas que califican esperan en `PendingAlert` y salen juntas.
  - `dedup_key` único por usuario: el mismo hecho (una ejecución, un resumen, una pausa) no se avisa dos veces aunque el trabajo se reintente."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, func, select

from app.config import settings
from app.db import (
    JobRow,
    Notification,
    NotificationChannel,
    NotificationDelivery,
    NotificationPref,
    PendingAlert,
    User,
    engine,
    utcnow,
)
from app.domain.job import Job
from app.notifications import channels, secrets
from app.observability import metrics
from app.scheduling import windows

log = logging.getLogger(__name__)
TOP_IN_BODY = 5
DIGEST_MAX = 50


class Deferred(Exception):
    """Aplaza la entrega sin gastar un intento (límite por hora)."""

    def __init__(self, seconds: float, reason: str):
        super().__init__(reason)
        self.seconds, self.reason = seconds, reason


# ------------------------------------------------------------------ preferencias
def get_prefs(s: Session, user_id: int) -> NotificationPref:
    p = s.get(NotificationPref, user_id)
    if p is None:
        p = NotificationPref(user_id=user_id)
        s.add(p)
        try:
            s.commit()
        except IntegrityError:
            s.rollback()
            p = s.get(NotificationPref, user_id)
        else:
            s.refresh(p)
    return p


def prefs_out(p: NotificationPref) -> dict:
    return {k: getattr(p, k) for k in ("auto_evaluate", "apply_to_manual", "auto_tailor", "tailor_daily_budget", "min_score", "timezone", "quiet_start",
                                       "quiet_end", "digest", "digest_hour", "digest_weekday")}


# ------------------------------------------------------------------ crear avisos
def out(n: Notification) -> dict:
    return {"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "job_id": n.job_id, "data": n.data, "read": n.read_at is not None,
            "created_at": windows.aware(n.created_at).isoformat()}


def create(s: Session, user_id: int, kind: str, title: str, body: str = "", *, job_id: int | None = None, data: dict | None = None,
           dedup_key: str | None = None, now: datetime | None = None, deliver: bool = True) -> Notification | None:
    """Crea el aviso en-app y, si `deliver`, planifica sus entregas por canal. None = ya existía (dedup_key)."""
    now = now or utcnow()
    if dedup_key and s.exec(select(Notification.id).where(Notification.user_id == user_id, Notification.dedup_key == dedup_key)).first():
        return None
    n = Notification(user_id=user_id, kind=kind, title=title[:200], body=body[:2000], job_id=job_id, data=data or {}, dedup_key=dedup_key, created_at=now)
    s.add(n)
    try:
        s.flush()
    except IntegrityError:                      # otro trabajador lo creó entre la comprobación y el insert
        s.rollback()
        return None
    metrics.inc("jh_notifications_total", kind=kind)
    if deliver:
        plan_deliveries(s, n, now)
    s.commit()
    s.refresh(n)
    return n


def notify_system(s: Session, user_id: int, kind: str, title: str, body: str, *, data: dict | None = None, dedup_key: str | None = None,
                  now: datetime | None = None) -> Notification | None:
    return create(s, user_id, kind, title, body, data=data, dedup_key=dedup_key, now=now)


def plan_deliveries(s: Session, n: Notification, now: datetime) -> list[NotificationDelivery]:
    """Una entrega (y su trabajo en cola) por canal activo. Dentro del silencio nocturno se programan para cuando termine."""
    from app.worker import queue
    prefs = get_prefs(s, n.user_id)
    when = windows.quiet_ends_at(now, prefs.timezone, prefs.quiet_start, prefs.quiet_end) or now
    chans = s.exec(select(NotificationChannel).where(NotificationChannel.user_id == n.user_id, NotificationChannel.enabled == True)).all()   # noqa: E712
    ds = []
    for ch in chans:
        d = NotificationDelivery(user_id=n.user_id, notification_id=n.id, channel_id=ch.id)
        s.add(d)
        s.flush()
        queue.enqueue(s, "deliver", ref_id=d.id, user_id=n.user_id, payload={"delivery_id": d.id}, run_after=when, priority=5,
                      max_attempts=settings.notify_max_attempts, dedup_key=f"deliver:{d.id}", now=now, commit=False)
        ds.append(d)
    return ds


def job_brief(row: JobRow, score: float | None = None) -> dict:
    j = Job.model_validate(row.data)
    return {"id": row.id, "title": j.title, "company": j.company, "location": j.location, "url": j.apply_url or j.url, "score": score}


def job_score(row: JobRow) -> float | None:
    """Puntaje 1-5: el de la evaluación; si aún no hay, el de la etapa 1 (0-100) llevado a la misma escala."""
    ev = row.evaluation or {}
    if isinstance(ev, dict) and ev.get("score") is not None:
        return float(ev["score"])
    return round(1 + 4 * row.stage1 / 100, 2) if row.stage1 is not None else None


def notify_new_jobs(s: Session, user_id: int, run_id: int, name: str, scored: list[tuple[JobRow, float]], prefs: NotificationPref,
                    now: datetime | None = None) -> Notification | None:
    """`scored`: ofertas NUEVAS de la ejecución que superan el umbral. Con resumen se guardan; si no, un solo aviso agrupado por ejecución."""
    now = now or utcnow()
    if not scored:
        return None
    scored = sorted(scored, key=lambda x: -x[1])
    if prefs.digest != "off":
        have = set(s.exec(select(PendingAlert.job_id).where(PendingAlert.user_id == user_id)).all())
        for row, sc in scored:
            if row.id not in have:
                s.add(PendingAlert(user_id=user_id, job_id=row.id, score=sc, created_at=now))
        s.commit()
        return None
    n = len(scored)
    lines = [f"{r_title(row)} ({sc:.1f}/5)" for row, sc in scored[:TOP_IN_BODY]]
    return create(s, user_id, "new_jobs", f"{n} oferta{'s' if n != 1 else ''} nueva{'s' if n != 1 else ''}" + (f" · {name}" if name else ""),
                  "\n".join(lines) + (f"\n... y {n - TOP_IN_BODY} más" if n > TOP_IN_BODY else ""),
                  job_id=scored[0][0].id if n == 1 else None,
                  data={"run_id": run_id, "search": name, "job_ids": [r.id for r, _ in scored], "jobs": [job_brief(r, sc) for r, sc in scored[:20]]},
                  dedup_key=f"run:{run_id}", now=now)


def r_title(row: JobRow) -> str:
    j = row.data or {}
    return f"{j.get('title', '?')} - {j.get('company', '')}".strip(" -")


# ------------------------------------------------------------------ resumen (digest)
def digest_slot(prefs: NotificationPref, now: datetime) -> tuple[str, datetime] | None:
    """(clave del periodo, instante de salida) del periodo vigente en la zona del usuario, o None si el resumen está apagado.
    Diario: clave = fecha local; semanal: año-semana ISO. El resumen sale cuando la hora local pasa de `digest_hour`."""
    if prefs.digest == "off":
        return None
    tz = windows.tz_of(prefs.timezone)
    local = windows.aware(now).astimezone(tz)
    hour = max(0, min(23, prefs.digest_hour))
    if prefs.digest == "daily":
        slot = local.replace(hour=hour, minute=0, second=0, microsecond=0)
        return f"d:{local.date().isoformat()}", slot
    monday = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    slot = (monday + timedelta(days=max(0, min(6, prefs.digest_weekday)))).replace(hour=hour)
    iso = local.isocalendar()
    return f"w:{iso[0]}-{iso[1]:02d}", slot


def flush_digests(now: datetime | None = None) -> int:
    """Genera los resúmenes vencidos. Idempotente y seguro con varios trabajadores: el periodo se reclama con un UPDATE condicional."""
    now = windows.aware(now or utcnow())
    made = 0
    with Session(engine) as s:
        uids = list(s.exec(select(NotificationPref.user_id).where(NotificationPref.digest != "off")).all())
    for uid in uids:
        with Session(engine) as s:
            p = s.get(NotificationPref, uid)
            slot = digest_slot(p, now) if p else None
            if not slot or now < slot[1].astimezone(now.tzinfo) or p.last_digest_key == slot[0]:
                continue
            key = slot[0]
            won = s.execute(update(NotificationPref).where(NotificationPref.user_id == uid, (NotificationPref.last_digest_key != key)
                                                          | (col(NotificationPref.last_digest_key) == None))   # noqa: E711
                            .values(last_digest_key=key)).rowcount
            if won != 1:
                s.rollback()
                continue
            pend = s.exec(select(PendingAlert).where(PendingAlert.user_id == uid).order_by(col(PendingAlert.score).desc())).all()
            rows = [(s.get(JobRow, a.job_id), a.score) for a in pend]
            rows = [(r, sc) for r, sc in rows if r is not None and r.status != "discarded"]
            for a in pend:
                s.delete(a)
            s.commit()
            if not rows:
                continue
            n = len(rows)
            label = "diario" if p.digest == "daily" else "semanal"
            top = rows[:DIGEST_MAX]
            body = "\n".join(f"{r_title(r)} ({sc:.1f}/5)" for r, sc in top[:10]) + (f"\n... y {n - 10} más" if n > 10 else "")
            if create(s, uid, "digest", f"Resumen {label}: {n} oferta{'s' if n != 1 else ''} nueva{'s' if n != 1 else ''}", body,
                      data={"period": key, "job_ids": [r.id for r, _ in top], "jobs": [job_brief(r, sc) for r, sc in top[:20]]},
                      dedup_key=f"digest:{key}", now=now):
                made += 1
    return made


# ------------------------------------------------------------------ entrega
def deliver(delivery_id: int, now: datetime | None = None, *, send=channels.send) -> dict:
    """Ejecuta UNA entrega (lo llama el trabajador). Lanza `channels.DeliveryError` (con `.retry`) o `Deferred`."""
    now = now or utcnow()
    with Session(engine) as s:
        d = s.get(NotificationDelivery, delivery_id)
        if d is None or d.status != "pending":
            return {"skipped": True}
        ch, n = s.get(NotificationChannel, d.channel_id), s.get(Notification, d.notification_id)
        if ch is None or n is None or not ch.enabled:
            d.status, d.last_error = "dead", "canal eliminado o desactivado"
            s.add(d)
            s.commit()
            return {"skipped": True}
        sent = s.exec(select(func.count()).select_from(NotificationDelivery).where(NotificationDelivery.channel_id == ch.id,
                                                                                  NotificationDelivery.status == "sent",
                                                                                  col(NotificationDelivery.delivered_at) >= now - timedelta(hours=1))).one()
        if sent >= settings.notify_rate_per_hour:
            metrics.inc("jh_notification_deliveries_total", channel=ch.kind, result="deferred")
            raise Deferred(600, f"límite de {settings.notify_rate_per_hour} avisos/hora en el canal")
        user = s.get(User, d.user_id)
        notif = out(n)
        jobs = list((n.data or {}).get("jobs") or [])
        try:
            secret = secrets.decrypt(ch.secret_enc) if ch.secret_enc else None
        except secrets.SecretError as e:
            raise channels.DeliveryError(str(e), retry=False)
        d.attempts += 1
        try:
            res = send(ch.kind, dict(ch.config or {}), secret, notif, jobs, d.id, user.email if user else None)
        except channels.DeliveryError as e:
            d.last_error, d.response_code = str(e)[:300], e.code
            ch.last_error = str(e)[:300]
            s.add_all([d, ch])
            s.commit()
            metrics.inc("jh_notification_deliveries_total", channel=ch.kind, result="error")
            raise
        d.status, d.delivered_at, d.response_code, d.last_error = "sent", now, res.get("code"), None
        ch.failures, ch.last_ok_at, ch.last_error = 0, now, None
        s.add_all([d, ch])
        s.commit()
        metrics.inc("jh_notification_deliveries_total", channel=ch.kind, result="sent")
        return {"sent": True, **res}


def mark_dead(delivery_id: int, error: str, now: datetime | None = None) -> None:
    """Entrega agotada (dead-letter): se registra y, tras N muertas seguidas, el canal se desactiva con un aviso en-app."""
    now = now or utcnow()
    with Session(engine) as s:
        d = s.get(NotificationDelivery, delivery_id)
        if d is None or d.status == "sent":
            return
        d.status, d.last_error = "dead", error[:300]
        ch = s.get(NotificationChannel, d.channel_id)
        s.add(d)
        if ch is not None:
            ch.failures += 1
            ch.last_error = error[:300]
            if ch.failures >= settings.notify_channel_max_failures and ch.enabled:
                ch.enabled = False
                s.add(ch)
                s.commit()
                create(s, d.user_id, "channel_disabled", f"Canal desactivado: {ch.label or ch.kind}",
                       f"Falló {ch.failures} veces seguidas ({error[:120]}). Revisa su configuración y vuelve a activarlo.", data={"channel_id": ch.id},
                       dedup_key=f"chan-off:{ch.id}:{ch.failures}", now=now, deliver=False)
                return
            s.add(ch)
        s.commit()


# ------------------------------------------------------------------ lectura (API)
def list_for(s: Session, user_id: int, *, unread: bool = False, after_id: int = 0, limit: int = 50) -> list[Notification]:
    stmt = select(Notification).where(Notification.user_id == user_id)
    if unread:
        stmt = stmt.where(col(Notification.read_at) == None)   # noqa: E711
    if after_id:
        return list(s.exec(stmt.where(col(Notification.id) > after_id).order_by(col(Notification.id)).limit(limit)).all())
    return list(s.exec(stmt.order_by(col(Notification.id).desc()).limit(limit)).all())


def purge(now: datetime | None = None, days: int | None = None) -> int:
    """Borra avisos LEÍDOS antiguos (los no leídos se conservan) y sus entregas."""
    now = now or utcnow()
    cutoff = now - timedelta(days=days or settings.worker_retention_days * 6)
    with Session(engine) as s:
        old = s.exec(select(Notification).where(col(Notification.read_at) != None, col(Notification.created_at) < cutoff)).all()   # noqa: E711
        for n in old:
            for d in s.exec(select(NotificationDelivery).where(NotificationDelivery.notification_id == n.id)).all():
                s.delete(d)
            s.delete(n)
        s.commit()
    return len(old)
