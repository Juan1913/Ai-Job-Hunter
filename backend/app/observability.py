"""Observabilidad sin dependencias pesadas: contexto de petición, logging JSON y métricas Prometheus."""
from __future__ import annotations

import json
import logging
import sys
import threading
from contextvars import ContextVar
from datetime import datetime, timezone

# Contexto por petición (se propaga a los hilos de FastAPI porque anyio copia el contexto).
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_var: ContextVar[int | None] = ContextVar("user_id", default=None)

LATENCY_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)


# ------------------------------------------------------------------ logging
class JsonFormatter(logging.Formatter):
    _RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "taskName"}

    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if (rid := request_id_var.get()):
            out["request_id"] = rid
        if (uid := user_id_var.get()) is not None:
            out["user_id"] = uid
        for k, v in record.__dict__.items():          # campos extra: log.info("x", extra={"k": 1})
            if k not in self._RESERVED and k not in out:
                out[k] = v
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


class _TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        ctx = " ".join(f"{k}={v}" for k, v in (("rid", request_id_var.get()), ("uid", user_id_var.get())) if v is not None)
        return f"{base} [{ctx}]" if ctx else base


def configure_logging(level: str = "INFO", as_json: bool = True) -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_jh", False):
            root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler._jh = True  # type: ignore[attr-defined]
    handler.setFormatter(JsonFormatter() if as_json else _TextFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(level.upper())
    # uvicorn ya registra cada petición con su propio formato: lo silenciamos (lo hace el middleware).
    logging.getLogger("uvicorn.access").disabled = True
    for noisy in ("httpx", "httpcore"):         # una línea INFO por llamada al LLM/scraping es ruido
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True


# ------------------------------------------------------------------ métricas
def _lbl(labels: dict[str, str]) -> tuple:
    return tuple(sorted(labels.items()))


def _esc(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _fmt(name: str, labels: tuple, value: float, extra: tuple = ()) -> str:
    items = labels + extra
    s = ",".join(f'{k}="{_esc(v)}"' for k, v in items)
    return f"{name}{{{s}}} {value:g}" if s else f"{name} {value:g}"


class Metrics:
    """Registro mínimo (contadores, gauges, histogramas) con salida en formato de texto Prometheus."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._help: dict[str, tuple[str, str]] = {}
        self._counters: dict[str, dict[tuple, float]] = {}
        self._gauges: dict[str, dict[tuple, float]] = {}
        self._hists: dict[str, dict[tuple, list]] = {}      # labels -> [buckets..., sum, count]
        self._buckets: dict[str, tuple] = {}
        self._gauge_fns: dict[str, callable] = {}

    def describe(self, name: str, kind: str, text: str) -> None:
        self._help[name] = (kind, text)

    def inc(self, name: str, value: float = 1, **labels) -> None:
        with self._lock:
            d = self._counters.setdefault(name, {})
            k = _lbl(labels)
            d[k] = d.get(k, 0) + value

    def set(self, name: str, value: float, **labels) -> None:
        with self._lock:
            self._gauges.setdefault(name, {})[_lbl(labels)] = value

    def gauge_fn(self, name: str, fn) -> None:
        """Gauge calculado al leer /metrics (cola del LLM, búsquedas en curso...)."""
        self._gauge_fns[name] = fn

    def observe(self, name: str, value: float, buckets: tuple = LATENCY_BUCKETS, **labels) -> None:
        with self._lock:
            self._buckets.setdefault(name, buckets)
            bks = self._buckets[name]
            row = self._hists.setdefault(name, {}).setdefault(_lbl(labels), [0] * len(bks) + [0.0, 0])
            for i, b in enumerate(bks):
                if value <= b:
                    row[i] += 1
            row[-2] += value
            row[-1] += 1

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._hists.clear()

    # --- lectura (útil en tests)
    def counter(self, name: str, **labels) -> float:
        return self._counters.get(name, {}).get(_lbl(labels), 0)

    def hist_count(self, name: str, **labels) -> int:
        row = self._hists.get(name, {}).get(_lbl(labels))
        return row[-1] if row else 0

    def render(self) -> str:
        lines: list[str] = []
        gauges = {n: dict(v) for n, v in self._gauges.items()}
        for n, fn in self._gauge_fns.items():
            try:
                val = fn()
            except Exception:  # un gauge roto no debe tumbar /metrics
                continue
            gauges.setdefault(n, {})[()] = val

        def head(name: str, default_kind: str) -> None:
            kind, text = self._help.get(name, (default_kind, name))
            lines.append(f"# HELP {name} {text}")
            lines.append(f"# TYPE {name} {kind}")

        with self._lock:
            for name, d in sorted(self._counters.items()):
                head(name, "counter")
                lines += [_fmt(name, k, v) for k, v in sorted(d.items())]
            for name, d in sorted(gauges.items()):
                head(name, "gauge")
                lines += [_fmt(name, k, v) for k, v in sorted(d.items())]
            for name, d in sorted(self._hists.items()):
                head(name, "histogram")
                bks = self._buckets[name]
                for k, row in sorted(d.items()):
                    for i, b in enumerate(bks):
                        lines.append(_fmt(f"{name}_bucket", k, row[i], (("le", f"{b:g}"),)))
                    lines.append(_fmt(f"{name}_bucket", k, row[-1], (("le", "+Inf"),)))
                    lines.append(_fmt(f"{name}_sum", k, row[-2]))
                    lines.append(_fmt(f"{name}_count", k, row[-1]))
        return "\n".join(lines) + "\n"


metrics = Metrics()
metrics.describe("jh_http_requests_total", "counter", "Peticiones HTTP por método, ruta y estado")
metrics.describe("jh_http_request_duration_seconds", "histogram", "Latencia HTTP en segundos")
metrics.describe("jh_http_in_flight", "gauge", "Peticiones HTTP en curso")
metrics.describe("jh_llm_calls_total", "counter", "Llamadas al LLM por modelo y resultado")
metrics.describe("jh_llm_latency_seconds", "histogram", "Latencia de generación del LLM")
metrics.describe("jh_llm_tokens_total", "counter", "Tokens del LLM (prompt/completion)")
metrics.describe("jh_llm_retries_total", "counter", "Reintentos del cliente LLM por motivo")
metrics.describe("jh_llm_queue_wait_seconds", "histogram", "Espera en la cola del LLM")
metrics.describe("jh_llm_queue_depth", "gauge", "Llamadas esperando turno para el LLM")
metrics.describe("jh_llm_active", "gauge", "Inferencias del LLM en ejecución")
metrics.describe("jh_searches_running", "gauge", "Búsquedas en segundo plano en curso (queued+running)")
metrics.describe("jh_rate_limited_total", "counter", "Peticiones rechazadas por rate limit o cuota")
metrics.describe("jh_login_failures_total", "counter", "Intentos de login fallidos")
