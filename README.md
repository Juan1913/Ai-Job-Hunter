# JobHunter

Un buscador de empleo de código abierto que trabaja por ti, pero te deja siempre al mando.

Rastrea ofertas en portales y en las páginas de empleo de las empresas, las puntúa según tu perfil, adapta tu CV a cada una **sin inventar nada** y te ayuda a seguir cada postulación. Y si quieres, puede postular por ti. Si no quieres, no lo hace: viene apagado.

`Python · FastAPI · React · TypeScript · SQLite o PostgreSQL · Scrapling · Playwright · Pulumi`

---

## Para qué sirve

**Encontrar ofertas.** Busca en portales como LinkedIn, Computrabajo, Magneto o Indeed, y en las páginas de carreras de empresas que usan Greenhouse, Lever, Ashby, Workable y similares. Puedes añadir tus propias páginas de empleo y buscar el rol que quieras, aunque no sea el de tu CV.

**Entender tu CV.** Subes un PDF o un DOCX (el formato Harvard incluido) y arma tu perfil. Tú lo revisas y lo corriges.

**Decirte cuáles valen la pena.** Cada oferta recibe una nota de 1 a 5 según tus habilidades, tu nivel, la ubicación, el idioma, el salario y qué tan reciente es, con las razones a la vista.

**Adaptar tu CV a cada oferta.** Genera un CV distinto para cada puesto (Harvard por defecto, en PDF o DOCX) que pasa los filtros ATS. Solo usa lo que de verdad tienes: si la oferta pide algo que no figura en tu perfil, entra al CV únicamente si tú confirmas que lo tienes.

**Filtrar y ordenar a tu gusto.** Por modalidad (remoto, híbrido o presencial), salario, especialidad, tipo de contrato, país y antigüedad. Puedes ordenar por mejor encaje, por las más recientes o por mayor salario.

**Ver dónde están.** Un mapamundi con las ofertas por ciudad o país. Es un SVG propio, así que no depende de ningún servicio externo.

**Seguir tus postulaciones.** Un tablero por etapas, con entrevistas y resultados. Además analiza por qué algunas no avanzan y te propone qué cambiar, siempre con la evidencia a la vista y avisando de que son hipótesis, porque las empresas casi nunca explican un rechazo.

**Trabajar mientras duermes.** Puedes programar búsquedas con tus filtros. Un trabajador las ejecuta solo y te avisa por la app, por webhook, ntfy, Telegram o correo.

## Cómo piensa el proyecto

1. **Tú decides.** Por defecto no envía nada. La auto-postulación es opcional, empieza apagada y en modo ensayo.
2. **No inventa.** Ni en tu CV ni en los formularios. Si no sabe algo, se detiene y te lo deja a ti.
3. **Sirve con o sin IA.** Puede usar un modelo local, una API externa (solo si das tu consentimiento) o ninguna IA, con reglas y un CV extractivo.
4. **Tus datos son tuyos.** Cada usuario ve solo lo suyo, puedes exportarlo o borrarlo todo, y los secretos se guardan cifrados.

## Para probarlo en tu máquina

Necesitas Python 3.12 o superior, Node 20 o superior y Chromium para Playwright.

```bash
# Backend
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium     # lo usan la auto-postulación y el modo stealth

# Frontend
cd ../frontend && npm install

# Y todo junto: API (8000), trabajador y frontend (5173)
cd .. && ./dev.sh
```

Abre <http://localhost:5173>, pulsa **Registrarse** (correo y una contraseña de 10 caracteres o más) y sube tu CV. `dev.sh` arranca sin IA y guarda los datos en `backend/jobhunter.db`; si prefieres otra configuración, pon las variables antes del comando. La documentación de la API queda en <http://localhost:8000/docs>.

Para correr las pruebas:

```bash
cd backend  && pytest -q && ruff check app tests      # unos 850 tests
cd frontend && npm run typecheck && npm run lint && npm test && npm run build
```

## Con IA o sin ella

| `JH_AI_MODE` | Qué significa | ¿Salen datos a terceros? |
|---|---|---|
| `none` | Sin IA: reglas, léxico y un CV extractivo que elige tus viñetas reales según la oferta | No |
| `local` | Un modelo pequeño en tu propio equipo con Ollama, en CPU y sin GPU | No |
| `api` | Una API externa (compatible con OpenAI o Anthropic), con tu consentimiento, un presupuesto diario y tus datos de contacto tapados | **Sí** |

Todo funciona en los tres modos, y cada respuesta te dice si usó IA y, si no, por qué. Hay más detalle en [docs/OPERACION.md](docs/OPERACION.md#modos-de-ia-jh_ai_mode).

## Auto-postulación (opcional)

Se activa en la pantalla **Auto-postulación**, con tres modos:

| Modo | Qué hace |
|---|---|
| Apagada | Nada. Es lo que viene por defecto. |
| Con tu aprobación | Prepara la postulación y te avisa. Solo se envía cuando tú pulsas «Aprobar». |
| Automática | Postula sola apenas aparece una oferta que cumple tus reglas (junto con las búsquedas programadas). |

Tú pones las reglas: el encaje mínimo, si solo las que «valen la pena», cuántas como máximo al día, si usa **tu CV general o uno hecho para cada puesto**, si incluye carta, qué libertad tiene en cada tipo de portal y una pausa de emergencia. Arranca en **modo ensayo**: rellena el formulario pero no lo envía, hasta que lo desactivas tú. Cada intento queda registrado, y lo que se envía pasa al seguimiento con el CV exacto que se usó.

Conviene saber esto antes de usarla:

- Solo funciona con portales de **formulario público**: Greenhouse, Lever, Ashby, Workable, Teamtailor, Recruitee, BambooHR y páginas de empleo genéricas. LinkedIn, Computrabajo, Indeed y otros piden iniciar sesión, así que ahí postulas tú.
- No inventa respuestas, no resuelve captchas y no marca casillas de marketing. Si el formulario pide algo que no sabe, se detiene y te dice qué falta.
- La probé contra **formularios reales en local, con navegador real**, pero no contra los portales de producción, que cambian su HTML cuando quieren. Es normal que algunos queden en «Termina tú».
- Automatizar postulaciones puede ir contra los términos de algunos portales y hacer que te bloqueen la cuenta. Empieza en ensayo, sigue con aprobación y usa un tope bajo.

## Cómo está armado

```
 navegador ──> React (Vite) ──/api──> FastAPI ──> SQLite o PostgreSQL
                                        │  └── cola de trabajos (tabla workitem)
                                        │
   trabajador (python -m app.worker) ───┘  búsquedas programadas · tareas lentas · avisos · auto-postulación
        └── Scrapling (scraping) + Playwright (formularios) + Ollama o una API (opcional)
```

```
backend/    FastAPI, SQLModel y Alembic: búsqueda, perfil, CV, evaluación, seguimiento y auto-postulación
frontend/   React y TypeScript: la lógica y lo visual van separados, y ESLint lo hace cumplir
infra/      Pulumi: backend, trabajador, Ollama, Postgres, Caddy con TLS y copias de seguridad
docs/       guía de operación, contrato OpenAPI y decisiones de arquitectura
```

El front se genera a partir del contrato OpenAPI (`docs/openapi.json`), así que si un cambio en la API rompe algo, el compilador te lo dice.

## Más documentación

- [docs/OPERACION.md](docs/OPERACION.md): desplegar en un VPS con Pulumi, variables de entorno, trabajador, avisos, copias de seguridad y límites conocidos.
- [docs/adr/](docs/adr): las decisiones de arquitectura, cortas.
- [frontend/README.md](frontend/README.md): las capas del front y sus comandos.
- [docs/openapi.json](docs/openapi.json): el contrato de la API.

## Estado actual

El proyecto está en construcción. Esto es lo que conviene tener presente:

- **Portales reales.** Los scrapers dependen del HTML de cada sitio, que cambia. En cada búsqueda verás en vivo qué fuentes responden y cuáles no.
- **Lectura del CV.** La probé con CV propios y en formato Harvard. Los de dos columnas con un diseño complicado todavía pueden leerse mal, así que revisa siempre el perfil que detecta.
- **Puntuación.** Es determinista y se puede explicar, pero no está calibrada para todos los perfiles. Ajusta el encaje mínimo según lo que veas.
- **Una sola instancia de API.** Los límites de tasa y la cola del LLM viven en memoria. La cola de trabajos, en cambio, sí es persistente. Hay más en [docs/OPERACION.md](docs/OPERACION.md#decisiones-y-límites).

## Úsalo con responsabilidad

Este proyecto automatiza tareas sobre sitios de terceros. Respeta los términos de uso y el `robots.txt` de cada portal, mantén una frecuencia razonable y no postules con datos falsos. Lo que envías es responsabilidad tuya.

## Contribuir

JobHunter es de código abierto y toda ayuda es bienvenida: scrapers para nuevos portales, mejoras en la lectura de CV, traducciones, pruebas y documentación. Abre un issue para contar lo que encontraste o lo que te gustaría, o manda un pull request. Antes de enviarlo, corre las pruebas y el lint (los comandos están arriba).

## Licencia

Código abierto bajo la licencia [MIT](LICENSE).
