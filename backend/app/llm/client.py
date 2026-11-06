"""Cliente LLM endurecido para un servidor local pequeño (Ollama / OpenAI-compatible) en CPU.

- Modo JSON nativo + reparación de JSON roto + reintentos acotados con backoff.
- Semáforo global FIFO justo entre usuarios: la CPU no soporta dos inferencias a la vez.
- Timeouts separados (conexión vs generación), num_ctx / num_predict / keep_alive configurables.
- Métricas (latencia, tokens, reintentos, cola) y errores tipificados.
- Backends: "ollama" (/api/chat), "openai" (/chat/completions: OpenAI, Groq, OpenRouter, Gemini compat, llama.cpp...) y
  "anthropic" (Messages API). En los dos externos: clave por cabecera (nunca en logs/errores), cola propia con más
  concurrencia, reintentos con Retry-After ante 429, redacción de datos de contacto y contabilidad de tokens.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque

import httpx

from app.ai.privacy import Redactor, redact_secrets
from app.config import settings
from app.llm.jsonrepair import repair_json
from app.observability import metrics, user_id_var

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ errores
class LLMError(RuntimeError):
    """Error del LLM. `kind` indica la causa: unavailable | timeout | invalid_json | busy."""
    kind = "unavailable"

    def __init__(self, message: str, kind: str | None = None):
        super().__init__(message)
        if kind:
            self.kind = kind


class LLMUnavailable(LLMError):
    kind = "unavailable"


class LLMTimeout(LLMError):
    kind = "timeout"


class LLMInvalidJSON(LLMError):
    kind = "invalid_json"


class LLMBusy(LLMError):
    """Demasiado tiempo esperando turno en la cola."""
    kind = "busy"


class LLMDisabled(LLMUnavailable):
    """La IA no está disponible para este usuario (modo none, sin consentimiento, sin clave o presupuesto agotado)."""
    kind = "disabled"

    def __init__(self, message: str, reason: str = "mode_none"):
        super().__init__(message)
        self.reason = reason
        self.permanent = True


# ------------------------------------------------------------------ cola justa
class _Waiter:
    __slots__ = ("event", "granted")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.granted = False


class FairGate:
    """Semáforo con cola FIFO por usuario y round-robin entre usuarios.

    Un usuario con 10 llamadas pendientes no deja sin turno a otro que llega después:
    se alternan usuarios y, dentro de cada uno, el orden es FIFO.
    """

    def __init__(self, slots: int = 1):
        self.slots = max(1, slots)
        self._lock = threading.Lock()
        self._active = 0
        self._queues: dict[object, deque[_Waiter]] = {}
        self._order: deque[object] = deque()

    @property
    def active(self) -> int:
        return self._active

    @property
    def queued(self) -> int:
        with self._lock:
            return sum(len(q) for q in self._queues.values())

    def acquire(self, key: object = None, timeout: float | None = None) -> float:
        """Bloquea hasta tener turno. Devuelve segundos esperados; lanza LLMBusy si agota `timeout`."""
        t0 = time.monotonic()
        with self._lock:
            if self._active < self.slots and not self._order:
                self._active += 1
                return 0.0
            w = _Waiter()
            if key not in self._queues:
                self._queues[key] = deque()
                self._order.append(key)
            self._queues[key].append(w)
        if not w.event.wait(timeout):
            with self._lock:
                if w.granted:                 # nos lo concedieron justo al vencer el plazo
                    return time.monotonic() - t0
                q = self._queues.get(key)
                if q and w in q:
                    q.remove(w)
                    if not q:
                        del self._queues[key]
                        self._order.remove(key)
            raise LLMBusy(f"El modelo está ocupado (más de {timeout:.0f}s en cola)")
        return time.monotonic() - t0

    def release(self) -> None:
        with self._lock:
            self._active -= 1
            self._dispatch()

    def _dispatch(self) -> None:
        while self._active < self.slots and self._order:
            key = self._order.popleft()
            q = self._queues[key]
            w = q.popleft()
            if q:
                self._order.append(key)       # round-robin: vuelve al final
            else:
                del self._queues[key]
            w.granted = True
            self._active += 1
            w.event.set()


_gate: FairGate | None = None
_gate_lock = threading.Lock()


def get_gate() -> FairGate:
    global _gate
    with _gate_lock:
        if _gate is None or _gate.slots != max(1, settings.llm_max_concurrency):
            _gate = FairGate(settings.llm_max_concurrency)
        return _gate


_api_gate: FairGate | None = None


def get_api_gate() -> FairGate:
    """Cola del modo api: el proveedor sí admite paralelismo, así que `llm_api_max_concurrency` > 1."""
    global _api_gate
    with _gate_lock:
        if _api_gate is None or _api_gate.slots != max(1, settings.llm_api_max_concurrency):
            _api_gate = FairGate(settings.llm_api_max_concurrency)
        return _api_gate


metrics.gauge_fn("jh_llm_queue_depth", lambda: get_gate().queued)
metrics.gauge_fn("jh_llm_active", lambda: get_gate().active)


# ------------------------------------------------------------------ transporte compartido
_shared: dict[str, httpx.Client] = {}
_shared_lock = threading.Lock()


def _timeouts() -> httpx.Timeout:
    return httpx.Timeout(connect=settings.llm_connect_timeout_s, read=settings.llm_timeout_s,
                         write=30.0, pool=settings.llm_connect_timeout_s)


def _shared_client(kind: str = "c") -> httpx.Client:
    with _shared_lock:
        c = _shared.get(kind)
        if c is None or c.is_closed:
            t = httpx.Timeout(connect=settings.llm_connect_timeout_s, read=settings.llm_api_timeout_s, write=30.0, pool=30.0) if kind == "api" else _timeouts()
            c = _shared[kind] = httpx.Client(timeout=t)
        return c


def close_shared() -> None:
    with _shared_lock:
        for c in _shared.values():
            c.close()
        _shared.clear()


def _retry_after(r: httpx.Response) -> float:
    """Segundos de la cabecera Retry-After (numérica); 1 s si no viene o no es interpretable."""
    try:
        return max(0.0, float(r.headers.get("retry-after", "")))
    except ValueError:
        return 1.0


# ------------------------------------------------------------------ cliente
class LLMClient:
    """Compatible hacia atrás: `complete(system, user)` y `complete_json(system, user)`."""

    def __init__(self, base_url: str | None = None, model: str | None = None, *,
                 backend: str | None = None, transport: httpx.BaseTransport | None = None,
                 sleep=time.sleep, api_key: str | None = None, headers: dict[str, str] | None = None,
                 external: bool = False, on_usage=None, retries: int | None = None, timeout_s: float | None = None):
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.llm_model_fast
        # "ollama" usa /api/chat (admite num_ctx y keep_alive); "openai" usa /chat/completions; "anthropic" /v1/messages.
        self.backend = backend or settings.llm_backend
        self._api_key = api_key or ""
        self._headers = dict(headers or {})
        self.external = external            # proveedor de terceros: redacta contacto, cola propia, secretos fuera de errores
        self._on_usage = on_usage           # callable(model, tokens_in, tokens_out)
        self._retries = settings.llm_retries if retries is None else retries
        self._timeout_s = timeout_s
        self._json_format_ok = True         # algunos servidores OpenAI-compatibles rechazan response_format
        self._own = httpx.Client(transport=transport, timeout=self._timeouts()) if transport else None
        self._sleep = sleep

    def _timeouts(self) -> httpx.Timeout:
        t = _timeouts()
        return httpx.Timeout(connect=t.connect, read=self._timeout_s or t.read, write=30.0, pool=t.pool)

    # --- HTTP
    @property
    def _http(self) -> httpx.Client:
        return self._own or (_shared_client("api") if self.external else _shared_client())

    @property
    def _root(self) -> str:
        return self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url

    def _request_headers(self) -> dict[str, str]:
        h = dict(self._headers)
        if self.backend == "anthropic":
            h["x-api-key"] = self._api_key
            h.setdefault("anthropic-version", settings.llm_api_anthropic_version)
        elif self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    def _payload(self, system: str, user: str, temperature: float, json_mode: bool, max_tokens: int | None) -> tuple[str, dict]:
        predict = max_tokens or settings.llm_num_predict
        if self.backend == "anthropic":
            # Messages API: sin temperature (los modelos 5.x la rechazan), sin prefill; JSON por instrucción + reparación.
            sys_txt = system + ("\n\nResponde ÚNICAMENTE con un objeto JSON válido, sin texto antes ni después." if json_mode else "")
            body = {"model": self.model, "max_tokens": max(predict, 2048), "system": sys_txt, "messages": [{"role": "user", "content": user}]}
            if settings.llm_api_anthropic_effort:
                body["output_config"] = {"effort": settings.llm_api_anthropic_effort}
            root = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
            return f"{root}/v1/messages", body
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        if self.backend == "ollama":
            body = {"model": self.model, "messages": msgs, "stream": False,
                    "keep_alive": settings.llm_keep_alive,
                    "options": {"temperature": temperature, "num_ctx": settings.llm_num_ctx, "num_predict": predict}}
            if json_mode and settings.llm_native_json:
                body["format"] = "json"
            return f"{self._root}/api/chat", body
        body = {"model": self.model, "messages": msgs, "temperature": temperature, "max_tokens": predict}
        if json_mode and settings.llm_native_json and self._json_format_ok:
            body["response_format"] = {"type": "json_object"}
        return f"{self.base_url}/chat/completions", body

    def _parse(self, data: dict) -> tuple[str, int, int]:
        if self.backend == "ollama":
            return data["message"]["content"], int(data.get("prompt_eval_count") or 0), int(data.get("eval_count") or 0)
        if self.backend == "anthropic":
            if data.get("stop_reason") == "refusal":
                err = LLMUnavailable("El proveedor se negó a responder a esta petición")
                err.permanent = True  # type: ignore[attr-defined]
                raise err
            text = "".join(b.get("text", "") for b in data.get("content", []) if isinstance(b, dict) and b.get("type") == "text")
            usage = data.get("usage") or {}
            return text, int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
        usage = data.get("usage") or {}
        return data["choices"][0]["message"]["content"], int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)

    def _safe(self, text: str) -> str:
        return redact_secrets(text, self._api_key)

    def _post_once(self, url: str, body: dict) -> str:
        try:
            r = self._http.post(url, json=body, headers=self._request_headers())
        except httpx.TimeoutException as e:
            raise LLMTimeout(f"El LLM no respondió a tiempo: {self._safe(str(e) or type(e).__name__)}") from e
        except httpx.HTTPError as e:
            raise LLMUnavailable(f"LLM no disponible: {self._safe(str(e) or type(e).__name__)}") from e
        if r.status_code >= 500 or r.status_code == 429 or r.status_code == 529:
            err = LLMUnavailable(f"LLM no disponible: HTTP {r.status_code}")
            if r.status_code in (429, 529):
                err.retry_after = _retry_after(r)  # type: ignore[attr-defined]
            raise err
        if r.status_code >= 400:
            if (r.status_code == 400 and self.backend == "openai" and "response_format" in body and "response_format" in r.text):
                self._json_format_ok = False          # el servidor no admite JSON nativo: reintenta sin él (y recuerda)
                body.pop("response_format", None)
                err = LLMUnavailable("El servidor no admite response_format; se reintenta sin él")
                err.retry_after = 0.0  # type: ignore[attr-defined]
                raise err
            # 4xx (modelo inexistente, clave inválida, petición inválida): reintentar no ayuda
            err = LLMUnavailable(f"El LLM rechazó la petición: HTTP {r.status_code} {self._safe(r.text[:200])}")
            err.permanent = True  # type: ignore[attr-defined]
            raise err
        try:
            text, pt, ct = self._parse(r.json())
        except LLMError:
            raise
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise LLMUnavailable(f"Respuesta inesperada del LLM: {e}") from e
        metrics.inc("jh_llm_tokens_total", pt, model=self.model, kind="prompt")
        metrics.inc("jh_llm_tokens_total", ct, model=self.model, kind="completion")
        if self._on_usage:
            self._on_usage(self.model, pt, ct)
        return text

    def _generate(self, system: str, user: str, temperature: float, json_mode: bool, max_tokens: int | None) -> str:
        """Una generación con su turno en la cola y reintentos de red/5xx/429 con backoff."""
        red = Redactor() if self.external else None
        if red:                                   # última barrera de privacidad: nada de contacto sale al proveedor
            system, user = red.apply(system), red.apply(user)
        url, body = self._payload(system, user, temperature, json_mode, max_tokens)
        key = user_id_var.get() or "system"
        gate = get_api_gate() if self.external else get_gate()
        waited = gate.acquire(key, settings.llm_queue_timeout_s)
        metrics.observe("jh_llm_queue_wait_seconds", waited)
        t0 = time.monotonic()
        try:
            attempt = 0
            while True:
                try:
                    out = self._post_once(url, body)
                    metrics.inc("jh_llm_calls_total", model=self.model, result="ok")
                    return red.restore(out) if red else out
                except LLMError as e:
                    if getattr(e, "permanent", False) or attempt >= self._retries:
                        metrics.inc("jh_llm_calls_total", model=self.model, result=e.kind)
                        raise
                    attempt += 1
                    metrics.inc("jh_llm_retries_total", reason=e.kind)
                    delay = settings.llm_backoff_s * (2 ** (attempt - 1))
                    ra = getattr(e, "retry_after", None)
                    if ra is not None:               # el proveedor dijo cuándo volver: se obedece (con tope)
                        delay = 0.0 if ra == 0 else max(delay, min(ra, settings.llm_api_retry_after_max_s))
                    log.warning("LLM %s: reintento %d en %.1fs", e.kind, attempt, delay)
                    self._sleep(delay)
                    if "response_format" in body and not self._json_format_ok:
                        body.pop("response_format", None)
        finally:
            metrics.observe("jh_llm_latency_seconds", time.monotonic() - t0, model=self.model)
            gate.release()

    # --- API pública
    def complete(self, system: str, user: str, temperature: float = 0.1, *, json_mode: bool = False,
                 max_tokens: int | None = None) -> str:
        return self._generate(system, user, temperature, json_mode, max_tokens)

    def complete_json(self, system: str, user: str, *, max_tokens: int | None = None):
        """Devuelve el JSON del modelo. Si viene roto se repara; si es irrecuperable se reintenta
        (hasta `llm_json_retries`) con la temperatura subiendo un poco para no repetir el fallo."""
        last: Exception | None = None
        for attempt in range(settings.llm_json_retries + 1):
            raw = self._generate(system, user, 0.1 + 0.2 * attempt, True, max_tokens)
            try:
                return repair_json(raw)
            except ValueError as e:
                last = e
                metrics.inc("jh_llm_retries_total", reason="invalid_json")
                log.warning("JSON inválido del modelo (intento %d): %s", attempt + 1, e)
                if attempt < settings.llm_json_retries:
                    self._sleep(settings.llm_backoff_s * (2 ** attempt))
        metrics.inc("jh_llm_calls_total", model=self.model, result="invalid_json")
        raise LLMInvalidJSON(f"JSON inválido del modelo: {last}") from last

    # --- operación
    def warmup(self) -> bool:
        """Carga el modelo en memoria con una generación mínima. Nunca lanza."""
        try:
            self.complete("Responde 'ok'.", "ok", max_tokens=1)
            return True
        except LLMError as e:
            log.warning("warmup de %s falló: %s", self.model, e)
            return False

    def close(self) -> None:
        if self._own:
            self._own.close()


def list_models(base_url: str | None = None, transport: httpx.BaseTransport | None = None) -> list[str]:
    """Modelos descargados en el servidor (Ollama /api/tags). Lanza LLMError si no hay servidor."""
    root = (base_url or settings.llm_base_url).rstrip("/")
    root = root[:-3] if root.endswith("/v1") else root
    try:
        with httpx.Client(transport=transport, timeout=httpx.Timeout(3.0)) as c:
            r = c.get(f"{root}/api/tags")
            r.raise_for_status()
            return [m.get("name", "") for m in r.json().get("models", [])]
    except httpx.TimeoutException as e:
        raise LLMTimeout(str(e)) from e
    except (httpx.HTTPError, ValueError) as e:
        raise LLMUnavailable(str(e)) from e


def model_present(wanted: str, available: list[str]) -> bool:
    """Ollama añade ':latest' si el nombre no lleva etiqueta."""
    names = set(available)
    return wanted in names or (":" not in wanted and f"{wanted}:latest" in names)


def warmup_models(models: list[str] | None = None) -> dict[str, bool]:
    wanted = models or sorted({settings.llm_model_fast, settings.llm_model_writer})
    return {m: LLMClient(model=m).warmup() for m in wanted}
