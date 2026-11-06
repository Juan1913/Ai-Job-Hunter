# JobHunter

**Buscador de empleo multiusuario, con IA opcional y bajo tu control.** Rastrea ofertas de portales y páginas de empresas, las puntúa contra tu perfil, adapta tu CV a cada una **sin inventar experiencia**, te deja seguir cada postulación y, si tú lo decides, postula por ti.

`Python · FastAPI · React · TypeScript · SQLite/PostgreSQL · Scrapling · Playwright · Pulumi`

---

## Qué hace

- **Encuentra.** Busca en portales de empleo (LinkedIn, Computrabajo, Magneto, Indeed y otros) y en las páginas de carreras de empresas (Greenhouse, Lever, Ashby, Workable...). Tú puedes añadir tus propias páginas de empleo y buscar **cualquier rol**, no solo el de tu CV.
- **Entiende tu CV.** Sube un PDF o DOCX (incluido el formato Harvard) y el sistema arma tu perfil. Tú lo revisas.
- **Puntúa.** Evalúa cada oferta de 1 a 5 contra tu perfil: habilidades, nivel, ubicación, idioma, salario y frescura, con vetos y razones explicadas.
- **Adapta el CV.** Genera un CV para cada oferta (Harvard por defecto, PDF o DOCX) que pasa filtros ATS. **Nada que no tengas entra al CV**: las habilidades que faltan solo se incluyen si tú las confirmas.
- **Filtra y ordena.** Por modalidad (remoto, híbrido, presencial), salario, especialidad, contrato, país, antigüedad, y por las más recientes o mejor encaje.
- **Muestra dónde.** Un mapamundi con las ofertas por ciudad o país, hecho en SVG, sin servicios externos.
- **Sigue tus postulaciones.** Tablero por etapas, entrevistas, resultados, y un **análisis de por qué no avanzan** (con evidencia y nivel de confianza) y qué cambiar.
- **Trabaja solo.** Búsquedas programadas con filtros, que corren 24 horas en un trabajador, con avisos en la app, webhook, ntfy, Telegram o correo.
- **Postula por ti, si lo activas.** Ver [Auto-postulación](#auto-postulación-opcional).

## Principios

1. **Tú decides.** Por defecto el sistema no envía nada. La auto-postulación es opcional, arranca apagada y en modo ensayo.
2. **No inventa.** Ni en el CV ni en los formularios. Ante algo que no sabe, se detiene y te lo deja a ti.
3. **Funciona con o sin IA.** Tres modos: modelo local (Ollama, CPU), API externa con tu consentimiento, o **sin IA** (reglas, léxico y CV extractivo).
4. **Tus datos son tuyos.** Cada usuario ve solo lo suyo; exportación y borrado de cuenta (RGPD); los secretos se guardan cifrados.

## Inicio rápido (local)

Requisitos: Python 3.12+, Node 20+ y Chromium para Playwright.

```bash
# Backend
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium          # lo usa la auto-postulación y el modo stealth

# Frontend
cd ../frontend && npm install

# Todo junto: API (8000) + trabajador + front (5173)
cd .. && ./dev.sh
```

Abre <http://localhost:5173>, pulsa **Registrarse** (correo y contraseña de 10+ caracteres) y sube tu CV. `dev.sh` arranca sin IA (`JH_AI_MODE=none`) y guarda la base en `backend/jobhunter.db`; cambia esas variables antes del comando si quieres otra cosa. La API queda documentada en <http://localhost:8000/docs>.

Tests y calidad:

```bash
cd backend  && pytest -q && ruff check app tests            # ~850 tests
cd frontend && npm run typecheck && npm run lint && npm test && npm run build
```

## Modos de IA

| `JH_AI_MODE` | Qué es | Datos a terceros |
|---|---|---|
| `none` | Sin IA: reglas, léxico, TF-IDF y CV extractivo (viñetas reales elegidas por relevancia) | No |
| `local` | Modelo pequeño en tu máquina con Ollama (CPU, sin GPU) | No |
| `api` | API externa (OpenAI-compatible o Anthropic), con consentimiento del usuario, presupuesto diario y contacto redactado | **Sí** |

El sistema funciona en los tres. Cada respuesta indica si usó IA y, si no, por qué. Detalle en [docs/OPERACION.md](docs/OPERACION.md#modos-de-ia-jh_ai_mode).

## Auto-postulación (opcional)

Se activa en la pantalla **Auto-postulación**:

| Modo | Qué hace |
|---|---|
| Apagada | Nada. Es el valor por defecto. |
| Con tu aprobación | Prepara la postulación y te avisa. Envía solo cuando pulsas «Aprobar». |
| Automática | Postula sola, apenas aparece una oferta que cumple tus reglas (con búsquedas programadas). |

Tú eliges el encaje mínimo, si solo las «que valen la pena», el tope diario, **tu CV general o uno hecho para cada puesto**, la carta opcional, el modo por tipo de portal y una pausa de emergencia. Empieza en **modo ensayo**: rellena el formulario sin enviarlo, hasta que lo desactivas a propósito. Cada intento queda registrado y lo enviado entra al seguimiento con el CV exacto.

**Límites honestos**

- Solo portales con **formulario público** (Greenhouse, Lever, Ashby, Workable, Teamtailor, Recruitee, BambooHR y páginas de empleo genéricas). LinkedIn, Computrabajo, Indeed y similares exigen iniciar sesión: ahí postulas tú.
- No inventa respuestas, no resuelve captchas y no marca casillas de marketing. Si el formulario pide algo que no sabe, se detiene y te lista lo que falta.
- Está probada contra **formularios locales reales con navegador real**, no contra los portales de producción, cuyo HTML cambia. Es normal que algunos queden en «Termina tú».
- Automatizar postulaciones puede ir contra los términos de algunos portales y arriesgar bloqueos de cuenta. Empieza en ensayo, luego con aprobación, y usa un tope bajo.

## Arquitectura

```
 navegador ──> React (Vite) ──/api──> FastAPI ──> SQLite / PostgreSQL
                                        │  └── cola de trabajos (tabla workitem)
                                        │
   worker (python -m app.worker) ───────┘  búsquedas programadas · tareas lentas · avisos · auto-postulación
        └── Scrapling (scraping) + Playwright (formularios) + Ollama / API (opcional)
```

```
backend/    FastAPI + SQLModel + Alembic: scouting, perfil, CV, evaluación, seguimiento, auto-postulación
frontend/   React + TypeScript + TanStack Query: lógica y visual separadas (la regla la hace cumplir ESLint)
infra/      Pulumi (Python): backend, trabajador, Ollama, Postgres, Caddy con TLS y copias de seguridad
docs/       OPERACION.md, OpenAPI exportado y decisiones de arquitectura (ADR)
```

El front se genera a partir del contrato OpenAPI (`docs/openapi.json`), así que un cambio de la API que lo rompa lo detecta el compilador.

## Documentación

- [docs/OPERACION.md](docs/OPERACION.md): despliegue en VPS con Pulumi, variables de entorno, trabajador, avisos, copias de seguridad y límites conocidos.
- [docs/adr/](docs/adr): decisiones de arquitectura, cortas.
- [frontend/README.md](frontend/README.md): capas del front y comandos.
- [docs/openapi.json](docs/openapi.json): contrato de la API.

## Estado y límites

Es un proyecto en construcción. Lo que conviene saber:

- **Portales reales:** los scrapers dependen del HTML de cada sitio y cambian. Revisa qué fuentes responden en cada búsqueda (el panel lo muestra en vivo).
- **Lectura de CV:** validada con CV propios y en formato Harvard; los CV de dos columnas con diseño complejo aún pueden leerse mal. Revisa siempre el perfil detectado.
- **Puntuación:** es determinista y explicable, pero no está calibrada para todos los perfiles; ajusta el encaje mínimo según tus resultados.
- **Una instancia de API:** los límites de tasa y la cola del LLM viven en memoria. La cola de trabajos sí es persistente. Más en [docs/OPERACION.md](docs/OPERACION.md#decisiones-y-límites).

## Uso responsable

Este proyecto automatiza tareas sobre sitios de terceros. Respeta los términos de uso y el `robots.txt` de cada portal, usa límites de frecuencia razonables y no lo emplees para postular con datos falsos. Eres responsable de lo que envías.

## Licencia

Por definir. Añade un archivo `LICENSE` antes de publicar el repositorio.
