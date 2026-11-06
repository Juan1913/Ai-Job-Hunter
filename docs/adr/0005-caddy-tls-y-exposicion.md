# 0005 — Caddy para TLS y qué se expone

**Decisión.** En el stack con `domain`, Caddy es el único contenedor con puertos publicados (80/443/udp): TLS automático
con Let's Encrypt, `request_body` máximo, HTTP/3. Solo reenvía `/api/*`, `/health` y `/ready`; `/metrics`, `/docs` y
`/openapi.json` devuelven 404 fuera. El backend confía en `X-Forwarded-For` (`TRUST_PROXY_HEADERS`) únicamente en ese
escenario, porque Caddy sobrescribe la cabecera. En local no hay proxy: el backend publica en `127.0.0.1`.

**Alternativas.** nginx + certbot (más piezas y cron de renovación); Traefik (más configuración para un caso simple).
