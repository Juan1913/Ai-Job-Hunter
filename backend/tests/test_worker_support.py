"""Utilidades compartidas por los tests del trabajador, la programación y los avisos (no contiene tests)."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.job import Job
from tests.shared import API, CV

MODULES = ("app.worker.queue", "app.worker.health", "app.worker.loop", "app.scheduling.service", "app.scheduling.cache", "app.scheduling.budget",
           "app.scheduling.automation", "app.notifications.service", "app.notifications.api", "app.scheduling.api")


class SimClock:
    """Reloj simulado: `now()` y `advance()`. Se inyecta sustituyendo `utcnow` en los módulos del trabajador."""

    def __init__(self, start: datetime):
        self.t = start
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self.t

    def advance(self, **kw) -> datetime:
        with self._lock:
            self.t += timedelta(**kw)
            return self.t


@pytest.fixture
def sim(monkeypatch):
    clock = SimClock(datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc))       # lunes 12:00 UTC
    import importlib
    for m in MODULES:
        mod = importlib.import_module(m)
        if hasattr(mod, "utcnow"):
            monkeypatch.setattr(mod, "utcnow", clock.now)
    return clock


class CountingSource:
    """Fuente simulada que cuenta cuántas veces se le pregunta (para probar la caché compartida)."""
    countries: set = set()

    def __init__(self, name: str, jobs_fn):
        self.name = name
        self._fn = jobs_fn if callable(jobs_fn) else (lambda: jobs_fn)
        self.calls = 0

    def search(self, q):
        self.calls += 1
        return [j.model_copy(deep=True) for j in self._fn()]


def mk_job(i: int, title: str = "Analista SOC", company: str | None = None) -> Job:
    return Job(title=f"{title} {i}", company=company or f"Empresa{i}", location="Bogotá", url=f"https://x/{i}", source="a",
               description="Requiere SIEM, AWS y respuesta a incidentes")


def onboard(client, headers, monkeypatch=None, roles=("analista soc",)):
    assert client.post(f"{API}/me/cv", files=CV, headers=headers).status_code == 200
    client.put(f"{API}/me/preferences", json={"countries": ["CO"], "target_roles": list(roles), "languages": ["es"]}, headers=headers)


def set_prefs(client, headers, **kw):
    r = client.put(f"{API}/me/notifications/preferences", json=kw, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()
