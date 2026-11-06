# JobHunter · frontend (React + TypeScript)

La lógica y lo visual están separados **y la regla se hace cumplir** (ESLint + `src/architecture.test.ts`):

| Capa | Dónde | Puede importar |
|---|---|---|
| Contrato y red | `src/api/` (`schema.d.ts` generado del OpenAPI, `client.ts`, `http.ts`) | nada de React |
| Lógica | `src/features/*/use*.ts(x)` (hooks con react-query) | `api`, `lib` |
| Visual | `src/features/*/ui/`, `src/ui/` | solo props, `lib` y **tipos** del contrato |
| Páginas | `src/pages/` | hooks + UI; deciden navegación |
| Puro | `src/lib/` | nada |

## Comandos
```
npm i
npm run gen:api   # regenera src/api/schema.d.ts desde ../docs/openapi.json
npm run dev       # http://localhost:5173 ; /api se redirige a VITE_API_PROXY (por defecto http://127.0.0.1:8000)
npm run typecheck && npm run lint && npm test && npm run build
```
Tras cambiar el backend: exportar el OpenAPI (`docs/openapi.json`) y correr `gen:api`; `tsc` marca lo que se rompió.

## Regla de oro en pantalla
Por defecto el sistema no postula solo (la auto-postulación es opcional, está en «Auto-postulación» y empieza apagada y en ensayo). «Ir a postular» abre la oferta original y registra la intención; las habilidades que faltan solo entran al CV si el usuario las confirma (`GapList`).
