"""Bucle del trabajador 24/7.

Cada vuelta (`tick`): latido -> recuperar huérfanos (lease vencido) -> programar búsquedas vencidas -> resúmenes vencidos -> mantenimiento ->
renovar leases de lo que corre -> reclamar trabajos hasta llenar `concurrency`. Los trabajos corren en hilos propios (daemon); el bucle nunca
se bloquea con ellos, así sigue latiendo y renovando leases aunque una búsqueda dure minutos.

Apagado ordenado (SIGTERM/SIGINT): deja de reclamar y de programar, espera hasta `worker_grace_s` a lo que corre y devuelve a la cola lo que no
terminó (sin gastar el intento). Si el proceso muere de golpe (kill -9, OOM) el lease vence y otro trabajador lo recupera."""
from __future__ import annotations

import logging
import signal
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from app.config import settings
from app.db import utcnow
from app.notifications import service as notif
from app.notifications.channels import DeliveryError
from app.notifications.service import Deferred
from app.observability import metrics
from app.scheduling import budget, cache
from app.scheduling import service as sched
from app.worker import health, queue
from app.worker.handlers import KINDS
from app.worker.queue import Claimed

log = logging.getLogger(__name__)
MAINTENANCE_EVERY = timedelta(hours=1)

metrics.describe("jh_worker_items_total", "counter", "Trabajos del trabajador por tipo y resultado")
metrics.describe("jh_worker_item_seconds", "histogram", "Duración de los trabajos del trabajador")
metrics.describe("jh_worker_inflight", "gauge", "Trabajos en curso en este trabajador")
metrics.describe("jh_worker_orphans_total", "counter", "Trabajos recuperados tras un lease vencido")
metrics.describe("jh_scheduled_runs_total", "counter", "Ejecuciones de búsquedas programadas lanzadas")
metrics.describe("jh_source_cache_total", "counter", "Caché compartida de fuentes: hit/miss/bypass")
metrics.describe("jh_notifications_total", "counter", "Avisos en-app creados por tipo")
metrics.describe("jh_notification_deliveries_total", "counter", "Entregas por canal y resultado")


class Worker:
    def __init__(self, worker_id: str | None = None, *, concurrency: int | None = None, clock: Callable[[], datetime] = utcnow,
                 rng=None, kinds: dict | None = None):
        self.id = worker_id or health.default_worker_id()
        self.concurrency = concurrency or settings.worker_concurrency
        self.clock = clock
        self.kinds = kinds or KINDS
        self.stop_event = threading.Event()
        self.started_at = clock()
        self._running: dict[int, tuple[Claimed, threading.Thread]] = {}
        self._lock = threading.Lock()
        self._last_beat: datetime | None = None
        self._last_maint: datetime | None = None
        self._lost: set[int] = set()
        self.stats = {"done": 0, "failed": 0, "dead": 0, "deferred": 0, "recovered": 0, "scheduled": 0, "digests": 0}
        import random
        self.rng = rng or random.random

    # ------------------------------------------------------------ ejecución de un trabajo
    def _run_item(self, item: Claimed) -> None:
        t0 = time.monotonic()
        kind = self.kinds.get(item.kind)
        result = "done"
        try:
            if kind is None:
                raise DeliveryError(f"tipo de trabajo desconocido: {item.kind}", retry=False)
            kind.run(item)
            if item.id in self._lost:
                result = "lost_lease"                    # otro trabajador lo recuperó: su resultado es el que cuenta
            elif not queue.complete(item.id, self.id, now=self.clock()):
                result = "lost_lease"
            else:
                self.stats["done"] += 1
        except Deferred as d:
            queue.defer(item, self.id, d.seconds, d.reason, now=self.clock())
            result, self.stats["deferred"] = "deferred", self.stats["deferred"] + 1
        except Exception as e:  # noqa: BLE001
            retry = getattr(e, "retry", True)
            log.warning("trabajo %s (%s) falló en el intento %s/%s: %s", item.id, item.kind, item.attempts, item.max_attempts, e,
                        exc_info=not isinstance(e, DeliveryError))
            outcome = queue.fail(item, self.id, f"{type(e).__name__}: {e}", retry=retry, now=self.clock(), rng=self.rng)
            result = outcome or "lost_lease"
            if outcome == "dead":
                self.stats["dead"] += 1
                self._dead(item, str(e))
            else:
                self.stats["failed"] += 1
        finally:
            metrics.inc("jh_worker_items_total", kind=item.kind, result=result)
            metrics.observe("jh_worker_item_seconds", time.monotonic() - t0, kind=item.kind)
            with self._lock:
                self._running.pop(item.id, None)
                self._lost.discard(item.id)

    def _dead(self, item: Claimed, error: str) -> None:
        log.error("trabajo %s (%s) a dead-letter: %s", item.id, item.kind, error)
        kind = self.kinds.get(item.kind)
        if kind:
            try:
                kind.dead(item, error)
            except Exception:  # noqa: BLE001
                log.exception("anotando dead-letter del trabajo %s", item.id)

    def _spawn(self, item: Claimed) -> None:
        th = threading.Thread(target=self._run_item, args=(item,), name=f"job-{item.kind}-{item.id}", daemon=True)
        with self._lock:
            self._running[item.id] = (item, th)
        th.start()

    # ------------------------------------------------------------ una vuelta
    def tick(self) -> int:
        """Una vuelta del bucle. Devuelve cuántos trabajos nuevos arrancó."""
        now = self.clock()
        draining = self.stop_event.is_set()
        if self._last_beat is None or (now - self._last_beat).total_seconds() >= settings.worker_heartbeat_s:
            self._beat("draining" if draining else "running", now)
        requeued, dead = queue.recover_orphans(now=now)
        if requeued or dead:
            self.stats["recovered"] += len(requeued) + len(dead)
            metrics.inc("jh_worker_orphans_total", len(requeued) + len(dead))
            log.warning("recuperados %s trabajos huérfanos (%s a dead-letter)", len(requeued), len(dead))
            for d in dead:
                self.stats["dead"] += 1
                self._dead(d, "lease vencido tras agotar los intentos")
        self._renew(now)
        if draining:
            return 0
        self._guard(lambda: self._schedule(now))
        self._guard(lambda: self._maintenance(now))
        started = 0
        with self._lock:
            free = self.concurrency - len(self._running)
        if free > 0:
            for item in queue.claim(self.id, limit=free, now=now):
                self._spawn(item)
                started += 1
        return started

    def _guard(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001  un fallo de programación/mantenimiento no debe parar el trabajador
            log.exception("fallo en el bucle del trabajador")

    def _schedule(self, now: datetime) -> None:
        self.stats["scheduled"] += sched.enqueue_due(now, rng=self.rng)
        self.stats["digests"] += notif.flush_digests(now)

    def _maintenance(self, now: datetime) -> None:
        if self._last_maint and now - self._last_maint < MAINTENANCE_EVERY:
            return
        self._last_maint = now
        log.info("mantenimiento: %s", {"trabajos": queue.purge(now=now), "cache": cache.purge(now), "presupuesto": budget.purge(now), "avisos": notif.purge(now), "latidos": health.purge(now=now)})

    def _renew(self, now: datetime) -> None:
        """Renueva el lease de lo que corre cuando le queda menos de la mitad."""
        with self._lock:
            items = [it for it, _ in self._running.values()]
        for it in items:
            if not queue.extend(it.id, self.id, now=now):
                self._lost.add(it.id)
                log.error("lease perdido del trabajo %s (%s): otro trabajador lo recuperó", it.id, it.kind)

    def _beat(self, state: str, now: datetime) -> None:
        with self._lock:
            n = len(self._running)
        self._guard(lambda: health.beat(self.id, state=state, running=n, concurrency=self.concurrency, stats=dict(self.stats),
                                        started_at=self.started_at, now=now))
        self._last_beat = now

    # ------------------------------------------------------------ ciclo de vida
    def running(self) -> int:
        with self._lock:
            return len(self._running)

    def drain(self, timeout: float | None = None) -> int:
        """Espera a los trabajos en curso. Devuelve cuántos quedan."""
        deadline = time.monotonic() + (settings.worker_grace_s if timeout is None else timeout)
        while self.running() and time.monotonic() < deadline:
            time.sleep(0.05)
        return self.running()

    def shutdown(self) -> None:
        """Apagado ordenado: espera a lo que corre y devuelve a la cola lo que no terminó (sin gastar el intento)."""
        self.stop_event.set()
        self._beat("draining", self.clock())
        left = self.drain()
        if left:
            with self._lock:
                pend = [it for it, _ in self._running.values()]
            for it in pend:
                queue.release(it.id, self.id, now=self.clock())
            log.warning("apagado: %s trabajos devueltos a la cola", len(pend))
        self._beat("stopped", self.clock())
        log.info("trabajador %s parado", self.id, extra={"stats": self.stats})

    def install_signals(self) -> None:
        def handler(signum, _frame):
            log.info("señal %s: apagado ordenado", signum)
            self.stop_event.set()
        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)

    def run_forever(self) -> None:
        log.info("trabajador %s listo (concurrencia %s)", self.id, self.concurrency)
        while not self.stop_event.is_set():
            self._guard(self.tick)
            self.stop_event.wait(settings.worker_poll_s)
        self.shutdown()

    def run_until_idle(self, *, max_ticks: int = 1000, settle: float = 0.02) -> int:
        """Para tests y `--once`: vueltas hasta que no quede nada listo ni en curso. Devuelve cuántos trabajos arrancó."""
        total = 0
        for _ in range(max_ticks):
            total += self.tick()
            self.drain(timeout=30)
            if not self.running() and queue.ready_count(self.clock()) == 0:
                break
            time.sleep(settle)
        return total
