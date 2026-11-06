"""Canales de salida: webhook firmado, SSRF, secretos cifrados, reintentos con backoff, dead-letter, límite por hora, ntfy/Telegram/email y SSE."""
import json
import time
from datetime import timedelta

import pytest
from sqlmodel import Session, select

from app.config import settings
from app.db import Notification, NotificationChannel, NotificationDelivery, WorkItem, engine
from app.notifications import channels, secrets
from app.notifications import service as svc
from app.worker.loop import Worker
from tests.test_notifications_core import N, _make, _uid
from tests.test_worker_support import sim  # noqa: F401  (fixture)


@pytest.fixture
def http(monkeypatch):
    """Sustituye la única función que hace I/O HTTP y salta la resolución DNS: registra las peticiones y responde lo que diga `.reply`."""
    monkeypatch.setattr(channels, "assert_public_url", lambda u: u)

    class H:
        calls: list = []
        reply = (200, "ok")

    h = H()
    h.calls = []

    def post(url, content, headers, timeout):
        h.calls.append({"url": url, "body": content, "headers": headers})
        if isinstance(h.reply, Exception):
            raise h.reply
        return h.reply

    monkeypatch.setattr(channels, "http_post", post)
    return h


def _chan(client, h, **kw):
    r = client.post(f"{N}/channels", json={"kind": "webhook", "url": "https://hook.example/x", "secret": "clave-hmac", **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_webhook_is_signed_with_hmac_and_verifiable_by_the_receiver(client, auth, http, sim):
    _chan(client, auth)
    n = _make(client, auth, data={"jobs": [{"id": 1, "title": "Analista", "company": "X", "url": "https://x/1", "score": 4.2}]}, title="2 ofertas")
    Worker("w", clock=sim.now).run_until_idle()
    call = http.calls[0]
    ts, sig = call["headers"]["X-JobHunter-Timestamp"], call["headers"]["X-JobHunter-Signature"]
    assert sig.startswith("sha256=") and call["headers"]["Content-Type"] == "application/json"
    assert channels.verify_signature("clave-hmac", ts, call["body"], sig)
    assert not channels.verify_signature("otra-clave", ts, call["body"], sig)                     # clave equivocada
    assert not channels.verify_signature("clave-hmac", ts, call["body"] + b" ", sig)              # cuerpo alterado
    assert not channels.verify_signature("clave-hmac", ts, call["body"], sig, now=time.time() + 3600)   # repetición tardía
    payload = json.loads(call["body"])
    assert payload["id"] == n.id and payload["title"] == "2 ofertas" and payload["jobs"][0]["title"] == "Analista"
    assert "email" not in call["body"].decode() and "password" not in call["body"].decode()      # nada del usuario que no sea el aviso
    d = client.get(f"{N}/deliveries", headers=auth).json()[0]
    assert d["status"] == "sent" and d["attempts"] == 1 and d["response_code"] == 200


@pytest.mark.parametrize("url", ["http://127.0.0.1:8080/x", "http://169.254.169.254/latest/meta-data", "http://10.0.0.5/h", "http://192.168.1.1/",
                                 "http://[::1]/h", "http://localhost/h", "ftp://8.8.8.8/x", "http://user:pw@8.8.8.8/h", "no-es-url"])
def test_ssrf_guard_rejects_internal_and_odd_urls_when_creating_a_channel(client, auth, url):
    r = client.post(f"{N}/channels", json={"kind": "webhook", "url": url, "secret": "s"}, headers=auth)
    assert r.status_code == 422, (url, r.text)
    r2 = client.post(f"{N}/channels", json={"kind": "ntfy", "server": url, "topic": "un-tema-largo-123"}, headers=auth)
    assert r2.status_code == 422


def test_public_literal_ip_is_accepted_without_dns(client, auth):
    assert client.post(f"{N}/channels", json={"kind": "webhook", "url": "http://8.8.8.8/hook", "secret": "s"}, headers=auth).status_code == 201


def test_ssrf_is_rechecked_at_send_time_even_if_the_stored_config_was_tampered(client, auth, monkeypatch, sim):
    called = []
    monkeypatch.setattr(channels, "http_post", lambda *a, **k: called.append(a) or (200, "ok"))
    ch = client.post(f"{N}/channels", json={"kind": "webhook", "url": "http://8.8.8.8/hook", "secret": "s"}, headers=auth).json()
    with Session(engine) as s:                                         # alguien edita la BD / el DNS apunta ahora a lo interno
        c = s.get(NotificationChannel, ch["id"])
        c.config = {"url": "http://169.254.169.254/latest"}
        s.add(c)
        s.commit()
    _make(client, auth)
    Worker("w", clock=sim.now).run_until_idle()
    assert called == []                                                # no se envió nada
    d = client.get(f"{N}/deliveries", headers=auth).json()[0]
    assert d["status"] == "dead" and d["attempts"] == 1 and "no permitida" in d["last_error"]      # y sin reintentos inútiles


def test_secrets_are_encrypted_at_rest_and_never_returned(client, auth, http, monkeypatch):
    ch = _chan(client, auth, secret="super-secreto-123")
    assert "secret" not in ch and ch["has_secret"] is True
    assert "super-secreto-123" not in json.dumps(client.get(f"{N}/channels", headers=auth).json())
    with Session(engine) as s:
        enc = s.exec(select(NotificationChannel)).one().secret_enc
    assert "super-secreto-123" not in enc and secrets.decrypt(enc) == "super-secreto-123"
    assert secrets.encrypt("x") != secrets.encrypt("x")                # nonce distinto cada vez
    with pytest.raises(secrets.SecretError):
        secrets.decrypt(enc[:-4] + "AAAA")                              # alterado
    monkeypatch.setattr(settings, "secrets_key", "otra-clave-maestra-distinta-0000000000")
    with pytest.raises(secrets.SecretError):
        secrets.decrypt(enc)                                             # otra clave maestra no descifra


def test_transient_failures_retry_with_backoff_then_dead_letter_and_channel_gets_disabled(client, auth, http, sim, monkeypatch):
    monkeypatch.setattr(settings, "notify_max_attempts", 3)
    monkeypatch.setattr(settings, "notify_channel_max_failures", 2)
    ch = _chan(client, auth)
    http.reply = (503, "caído")
    _make(client, auth, title="a")
    w = Worker("w", clock=sim.now)
    t0 = sim.now()
    gaps = []
    for _ in range(3):
        w.run_until_idle()
        with Session(engine) as s:
            it = s.exec(select(WorkItem).where(WorkItem.kind == "deliver")).one()
            if it.state == "queued":
                gaps.append((it.run_after - sim.now()).total_seconds())
                sim.advance(seconds=gaps[-1] + 1)
    assert len(http.calls) == 3 and gaps[1] > gaps[0] * 1.2            # backoff creciente (30 s, 60 s con jitter)
    d = client.get(f"{N}/deliveries", headers=auth).json()[0]
    assert d["status"] == "dead" and d["attempts"] == 3 and "503" in d["last_error"]
    assert sim.now() > t0
    http.calls.clear()
    http.reply = (400, "mal")                                          # 4xx: no se reintenta
    _make(client, auth, title="b")
    w.run_until_idle()
    assert len(http.calls) == 1
    chans = client.get(f"{N}/channels", headers=auth).json()["items"]
    assert chans[0]["enabled"] is False and chans[0]["failures"] == 2   # 2 muertas seguidas: se desactiva
    with Session(engine) as s:
        assert s.exec(select(Notification).where(Notification.kind == "channel_disabled")).one().data["channel_id"] == ch["id"]
    r = client.patch(f"{N}/channels/{ch['id']}", json={"enabled": True}, headers=auth).json()
    assert r["enabled"] and r["failures"] == 0


def test_rate_limit_per_hour_defers_without_burning_attempts(client, auth, http, sim, monkeypatch):
    monkeypatch.setattr(settings, "notify_rate_per_hour", 2)
    _chan(client, auth)
    for i in range(4):
        _make(client, auth, title=f"n{i}")
    w = Worker("w", clock=sim.now)
    w.run_until_idle()
    assert len(http.calls) == 2                                         # solo 2 en la hora
    with Session(engine) as s:
        its = s.exec(select(WorkItem).where(WorkItem.kind == "deliver", WorkItem.state == "queued")).all()
        assert len(its) == 2 and all(i.attempts == 0 for i in its)
    sim.advance(minutes=61)
    w.run_until_idle()
    assert len(http.calls) == 4


def test_a_notification_is_delivered_once_per_channel_even_if_the_item_is_retried(client, auth, http, sim):
    _chan(client, auth)
    _chan(client, auth, url="https://otro.example/y")
    _make(client, auth)
    w = Worker("w", clock=sim.now)
    w.run_until_idle()
    assert len(http.calls) == 2
    with Session(engine) as s:
        d = s.exec(select(NotificationDelivery)).first()
        from app.worker import queue
        queue.enqueue(s, "deliver", ref_id=d.id, payload={}, now=sim.now())            # un duplicado del trabajo (p. ej. tras una caída)
    w.run_until_idle()
    assert len(http.calls) == 2                                                         # la entrega ya enviada no se repite


def test_telegram_ntfy_and_email_channels(client, auth, http, monkeypatch, sim):
    r = client.post(f"{N}/channels", json={"kind": "telegram", "chat_id": "123456"}, headers=auth)
    assert r.status_code == 422                                                         # falta el token del bot
    client.post(f"{N}/channels", json={"kind": "telegram", "chat_id": "123456", "secret": "999:TOKEN"}, headers=auth)
    client.post(f"{N}/channels", json={"kind": "ntfy", "topic": "mi-tema-secreto-9", "secret": "tk_abc"}, headers=auth)
    assert client.post(f"{N}/channels", json={"kind": "email"}, headers=auth).status_code == 422       # el operador no configuró SMTP
    monkeypatch.setattr(settings, "smtp_host", "smtp.example")
    monkeypatch.setattr(settings, "smtp_from", "jh@example.com")
    sent = []
    monkeypatch.setattr(channels, "smtp_send", lambda msg: sent.append(msg))
    client.post(f"{N}/channels", json={"kind": "email", "url": "http://evil.example"}, headers=auth)
    with Session(engine) as s:
        from app.notifications import service
        service.create(s, _uid(client, auth), "new_jobs", "3 ofertas", "cuerpo", data={"jobs": [{"title": "T", "company": "C", "url": "https://x/1", "score": 4.0}]})
    Worker("w", clock=sim.now).run_until_idle()
    urls = {c["url"] for c in http.calls}
    assert "https://api.telegram.org/bot999:TOKEN/sendMessage" in urls and "https://ntfy.sh/mi-tema-secreto-9" in urls
    nt = next(c for c in http.calls if "ntfy" in c["url"])
    assert nt["headers"]["Authorization"] == "Bearer tk_abc" and b"T - C" in nt["body"]
    assert len(sent) == 1 and sent[0]["To"] == "luis@x.com" and sent[0]["From"] == "jh@example.com"      # SIEMPRE al email de la cuenta


def test_error_messages_never_leak_the_bot_token(client, auth, http, sim, monkeypatch):
    monkeypatch.setattr(settings, "notify_max_attempts", 1)
    client.post(f"{N}/channels", json={"kind": "telegram", "chat_id": "123456", "secret": "999:TOKEN"}, headers=auth)
    http.reply = RuntimeError("fallo conectando a https://api.telegram.org/bot999:TOKEN/sendMessage")
    _make(client, auth)
    Worker("w", clock=sim.now).run_until_idle()
    d = client.get(f"{N}/deliveries", headers=auth).json()[0]
    ch = client.get(f"{N}/channels", headers=auth).json()["items"][0]
    assert "999:TOKEN" not in json.dumps([d, ch]) and "bot***" in d["last_error"]


def test_channel_test_endpoint_sends_through_the_queue(client, auth, http, sim):
    ch = _chan(client, auth)
    r = client.post(f"{N}/channels/{ch['id']}/test", headers=auth)
    assert r.status_code == 202
    Worker("w", clock=sim.now).run_until_idle()
    assert len(http.calls) == 1 and json.loads(http.calls[0]["body"])["kind"] == "test"


# ------------------------------------------------------------------ tiempo real
def test_sse_stream_replays_after_last_event_id_and_is_private(client, auth, monkeypatch):
    from tests.conftest import register
    monkeypatch.setattr(settings, "notify_stream_max_s", 1.5)
    n1, n2 = _make(client, auth, title="uno"), _make(client, auth, title="dos")
    other = register(client, "otra@x.com")
    _make(client, other, title="ajeno")
    events = []
    with client.stream("GET", f"{N}/stream", headers={**auth, "Last-Event-ID": str(n1.id)}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:]))
    assert [e["id"] for e in events] == [n2.id] and events[0]["title"] == "dos"               # solo lo posterior y solo lo propio
    assert client.get(f"{N}/stream").status_code == 401


def test_long_poll_waits_for_new_notifications(client, auth):
    import threading
    n1 = _make(client, auth)
    threading.Timer(0.7, lambda: _make(client, auth, title="llegó")).start()
    t0 = time.monotonic()
    r = client.get(N, params={"after_id": n1.id, "wait": 5}, headers=auth).json()
    assert [i["title"] for i in r["items"]] == ["llegó"] and time.monotonic() - t0 < 4


def test_purge_removes_only_old_read_notifications(client, auth, sim):
    from app.db import utcnow
    a, b, c = _make(client, auth), _make(client, auth), _make(client, auth)
    with Session(engine) as s:
        for nid, read, age in ((a.id, True, 400), (b.id, True, 1), (c.id, False, 400)):
            n = s.get(Notification, nid)
            n.created_at = utcnow() - timedelta(days=age)
            n.read_at = utcnow() if read else None
            s.add(n)
        s.commit()
    assert svc.purge(utcnow()) == 1
    with Session(engine) as s:
        assert {n.id for n in s.exec(select(Notification)).all()} == {b.id, c.id}
