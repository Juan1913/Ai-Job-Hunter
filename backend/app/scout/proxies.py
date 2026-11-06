"""Proxies opcionales (JH_PROXIES) con round-robin POR DOMINIO y retirada temporal de los que fallan.

* Formatos admitidos: `http://host:port`, `https://...`, `socks5://user:pass@host:port` (también socks4/socks5h),
  `user:pass@host:port`, `host:port` y `host:port:user:pass` (el de muchos proveedores). Sin esquema = http.
* Round-robin por dominio: cada host lleva su propio cursor, así varias fuentes sobre dominios distintos no se
  pisan y un mismo dominio reparte sus peticiones entre todas las salidas.
* Si un proxy falla (error de red/407/5xx del túnel) se retira con cooldown creciente (x2 hasta un tope); un
  éxito lo rehabilita. Si TODOS están retirados se devuelve None: se sale directo (la búsqueda no se rompe).
* Scrapling/curl_cffi habla HTTP y SOCKS de forma nativa; el navegador (StealthyFetcher) admite HTTP(S) con
  usuario/clave y SOCKS sin autenticación (límite de Chromium)."""
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit

_SCHEMES = ("http", "https", "socks4", "socks5", "socks5h")


def parse_proxy(raw: str) -> str | None:
    """Normaliza una entrada a URL con esquema; None si no es utilizable."""
    s = (raw or "").strip()
    if not s or s.lower() == "localhost":
        return None
    if "://" in s:
        scheme, rest = s.split("://", 1)
        if scheme.lower() not in _SCHEMES or not rest:
            return None
        s = f"{scheme.lower()}://{rest}"
    else:
        parts = s.split(":")
        if len(parts) == 4 and parts[1].isdigit():          # host:port:user:pass
            host, port, user, pwd = parts
            s = f"http://{quote(user, safe='')}:{quote(pwd, safe='')}@{host}:{port}"
        else:
            s = f"http://{s}"
    u = urlsplit(s)
    try:
        port = u.port
    except ValueError:
        return None
    return s if u.hostname and port else None


def parse_proxies(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for item in re.split(r"[,\s;]+", text or ""):
        p = parse_proxy(item)
        if p:
            seen[p] = None
    return list(seen)


def mask(proxy: str) -> str:
    """Para logs: sin usuario ni clave."""
    u = urlsplit(proxy)
    return f"{u.scheme}://{u.hostname}:{u.port}"


def browser_proxy(proxy: str) -> dict:
    """Formato de proxy que espera el navegador (Playwright/patchright)."""
    u = urlsplit(proxy)
    out = {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
    if u.username:
        out["username"], out["password"] = u.username, u.password or ""
    return out


@dataclass
class _State:
    fails: int = 0
    until: float = 0.0
    ok: int = 0


@dataclass
class ProxyPool:
    proxies: list[str] = field(default_factory=list)
    cooldown: float = 60.0
    cooldown_max: float = 900.0
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self):
        self._state = {p: _State() for p in self.proxies}
        self._cursor: dict[str, int] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.proxies)

    def pick(self, host: str) -> str | None:
        """Siguiente proxy disponible para `host` (round-robin por dominio)."""
        if not self.proxies:
            return None
        with self._lock:
            now, n = self.clock(), len(self.proxies)
            start = self._cursor.get(host, 0)
            for i in range(n):
                idx = (start + i) % n
                p = self.proxies[idx]
                if self._state[p].until <= now:
                    self._cursor[host] = (idx + 1) % n
                    return p
            return None

    def available(self, proxy: str) -> bool:
        with self._lock:
            st = self._state.get(proxy)
            return bool(st and st.until <= self.clock())

    def report(self, proxy: str | None, ok: bool) -> None:
        if not proxy or proxy not in self._state:
            return
        with self._lock:
            st = self._state[proxy]
            if ok:
                st.fails, st.until, st.ok = 0, 0.0, st.ok + 1
            else:
                st.fails += 1
                st.until = self.clock() + min(self.cooldown_max, self.cooldown * 2 ** (st.fails - 1))

    def status(self) -> list[dict]:
        now = self.clock()
        with self._lock:
            return [{"proxy": mask(p), "available": s.until <= now, "fails": s.fails, "ok": s.ok,
                     "retry_in_s": max(0, round(s.until - now))} for p, s in self._state.items()]


_pool: ProxyPool | None = None
_pool_lock = threading.Lock()


def get_pool() -> ProxyPool:
    """Pool global construido de settings.proxies (se lee una vez; `reset_pool()` en tests)."""
    global _pool
    with _pool_lock:
        if _pool is None:
            from app.config import settings
            _pool = ProxyPool(parse_proxies(settings.proxies), settings.proxy_cooldown_s, settings.proxy_cooldown_max_s)
        return _pool


def set_pool(pool: ProxyPool | None) -> None:
    global _pool
    with _pool_lock:
        _pool = pool


def reset_pool() -> None:
    set_pool(None)
