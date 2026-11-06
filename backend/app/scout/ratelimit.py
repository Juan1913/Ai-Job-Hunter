"""Rate limit por dominio con token bucket seguro entre hilos.

Las fuentes se ejecutan en paralelo (ThreadPoolExecutor), pero varias pueden pegarle al mismo
dominio (p. ej. 5 países de Computrabajo): el límite es por host, no por fuente, para no
martillar a nadie."""
import random
import threading
import time
from collections.abc import Callable
from urllib.parse import urlsplit


class TokenBucket:
    """`rate` tokens/s, hasta `burst` acumulados. `reserve()` descuenta un token y devuelve cuántos
    segundos hay que esperar (0 si había). Las reservas se encolan, así que es justo entre hilos."""

    def __init__(self, rate: float, burst: float = 1.0, clock: Callable[[], float] = time.monotonic):
        if rate <= 0:
            raise ValueError("rate debe ser > 0")
        self.rate, self.burst, self._clock = rate, max(1.0, burst), clock
        self.base_rate = rate
        self._tokens, self._last = self.burst, clock()
        self._lock = threading.Lock()

    def scale(self, factor: float, floor: float = 0.05) -> None:
        """Frena (factor < 1) o restituye (factor > 1) el ritmo; nunca por encima del original ni por debajo de `floor`."""
        with self._lock:
            self.rate = min(self.base_rate, max(floor, self.rate * factor))

    def reserve(self) -> float:
        with self._lock:
            now = self._clock()
            self._tokens = min(self.burst, self._tokens + (now - self._last) * self.rate)
            self._last = now
            self._tokens -= 1.0
            return 0.0 if self._tokens >= 0 else -self._tokens / self.rate

    def acquire(self, sleep: Callable[[float], None] = time.sleep) -> float:
        wait = self.reserve()
        if wait > 0:
            sleep(wait)
        return wait


# (sufijo de host) -> (peticiones/s, ráfaga). El más específico que coincida gana.
DEFAULT_LIMITS: dict[str, tuple[float, float]] = {
    "linkedin.com": (0.4, 1),
    "indeed.com": (0.5, 1),
    "computrabajo.com": (1.0, 2),
    "magneto365.com": (1.0, 2),
    "elempleo.com": (1.0, 2),
    "bumeran.com.mx": (0.5, 1), "bumeran.com.ar": (0.5, 1), "bumeran.com.pe": (0.5, 1),
    "bumeran.com.ec": (0.5, 1), "bumeran.com.pa": (0.5, 1),
    "zonajobs.com.ar": (0.5, 1), "laborum.cl": (0.5, 1),
    "occ.com.mx": (0.7, 1),
    "trabajosdiarios.com": (0.5, 1), "trabajosdiarios.co.cr": (0.5, 1),     # robots.txt: Crawl-delay 2
    "mercadolibre.com": (0.5, 1),
    "hn.algolia.com": (2.0, 2),
}
FALLBACK_LIMIT = (2.0, 3)   # APIs/ATS y cualquier otro host


class RateLimiter:
    def __init__(self, limits: dict[str, tuple[float, float]] | None = None, fallback: tuple[float, float] = FALLBACK_LIMIT,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.limits, self.fallback, self._clock, self._sleep = limits or DEFAULT_LIMITS, fallback, clock, sleep
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()
        self.enabled = True

    def _bucket(self, host: str) -> TokenBucket:
        with self._lock:
            b = self._buckets.get(host)
            if b is None:
                match = [s for s in self.limits if host == s or host.endswith("." + s)]
                rate, burst = self.limits[max(match, key=len)] if match else self.fallback
                b = self._buckets[host] = TokenBucket(rate, burst, self._clock)
            return b

    def acquire(self, url_or_host: str) -> float:
        if not self.enabled:
            return 0.0
        return self._bucket(host_of(url_or_host)).acquire(self._sleep)

    def slow_down(self, url_or_host: str, factor: float = 0.5) -> None:
        self._bucket(host_of(url_or_host)).scale(factor)

    def recover(self, url_or_host: str) -> None:
        self._bucket(host_of(url_or_host)).scale(2.0)


def host_of(url_or_host: str) -> str:
    return ((urlsplit(url_or_host).hostname or url_or_host) if "/" in url_or_host else url_or_host).lower()


limiter = RateLimiter()


# ---------------------------------------------------------------- circuit breaker por dominio
class CircuitBreaker:
    """Tras un 429 (o varios 403 seguidos) el dominio se "abre": las peticiones fallan al instante (la fuente queda
    `blocked`, no se martillea) durante un cooldown que crece x2 por cada fallo consecutivo (con jitter y tope).
    Al vencer deja pasar UNA petición de prueba (semi-abierto): si funciona se reanuda y se resetea; si falla,
    vuelve a abrirse con el cooldown siguiente. Seguro entre hilos."""

    SOFT_LIMIT = 3          # 403/captcha seguidos que equivalen a un 429
    PROBE_WAIT = 5.0        # mientras otro hilo prueba, el resto espera/falla rápido

    def __init__(self, base: float = 30.0, cap: float = 900.0, clock: Callable[[], float] = time.monotonic,
                 jitter: Callable[[], float] = lambda: random.uniform(0, 0.25)):
        self.base, self.cap, self._clock, self._jitter = base, cap, clock, jitter
        self._st: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _get(self, host: str) -> dict:
        return self._st.setdefault(host, {"strikes": 0, "soft": 0, "until": 0.0, "probing": False})

    def check(self, host: str) -> float:
        """0 = puede pasar; >0 = segundos que el circuito sigue abierto."""
        with self._lock:
            st = self._st.get(host)
            if not st or st["strikes"] == 0:
                return 0.0
            now = self._clock()
            if now < st["until"]:
                return st["until"] - now
            if st["probing"]:
                return self.PROBE_WAIT
            st["probing"] = True
            return 0.0

    def failure(self, host: str, retry_after: float | None = None) -> float:
        """429: abre (o re-abre) el circuito. Devuelve el cooldown aplicado."""
        with self._lock:
            st = self._get(host)
            st["strikes"] += 1
            st["probing"], st["soft"] = False, 0
            wait = min(self.cap, max(retry_after or 0.0, self.base * 2 ** (st["strikes"] - 1)))
            wait *= 1 + self._jitter()
            st["until"] = self._clock() + wait
            return wait

    def soft_failure(self, host: str) -> float:
        """403/captcha: solo abre tras SOFT_LIMIT seguidos (suelen ser por URL, no por dominio)."""
        with self._lock:
            st = self._get(host)
            st["soft"] += 1
            hit = st["soft"] >= self.SOFT_LIMIT
        return self.failure(host) if hit else 0.0

    def success(self, host: str) -> bool:
        """Devuelve True si el circuito estaba abierto/semiabierto (se reanuda)."""
        with self._lock:
            st = self._st.get(host)
            if not st:
                return False
            was = st["strikes"] > 0
            st["strikes"], st["soft"], st["until"], st["probing"] = 0, 0, 0.0, False
            return was

    def state(self, host: str) -> str:
        with self._lock:
            st = self._st.get(host)
            if not st or st["strikes"] == 0:
                return "closed"
            return "open" if self._clock() < st["until"] else "half_open"

    def reset(self) -> None:
        with self._lock:
            self._st.clear()


breaker = CircuitBreaker()
