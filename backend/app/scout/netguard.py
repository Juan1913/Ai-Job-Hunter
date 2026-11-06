"""Protección SSRF: los usuarios pueden registrar URLs de empresas; solo se aceptan sitios públicos."""
import ipaddress
import socket
from urllib.parse import urlsplit


class UnsafeURL(ValueError):
    pass


def assert_public_url(url: str) -> str:
    p = urlsplit(url.strip())
    if p.scheme not in ("http", "https") or not p.hostname:
        raise UnsafeURL("Solo se aceptan URLs http(s)")
    if p.username or p.password:
        raise UnsafeURL("La URL no puede incluir credenciales")
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise UnsafeURL(f"No se pudo resolver el dominio: {p.hostname}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise UnsafeURL("La URL apunta a una red privada o interna")
    return url.strip()
