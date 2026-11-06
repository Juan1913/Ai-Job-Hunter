import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

// Regla de arquitectura: LÓGICA y VISUAL separadas.
//  - features/*/ui y src/ui son puramente visuales: reciben props y pintan. No pueden tocar la API, react-query, el router ni hooks de datos.
//  - La lógica (hooks, cliente) vive fuera de ui/ y no importa componentes visuales de otras features.
const forbiddenInUi = [
  { group: ["@/api", "@/api/*", "**/api/*"], message: "Un componente visual no habla con la API: recibe datos por props." },
  { group: ["@tanstack/*"], message: "react-query pertenece a los hooks, no a la capa visual." },
  { group: ["react-router-dom"], message: "La navegación la decide la página (contenedor); la UI recibe callbacks." },
  { group: ["@/features/*/use*", "../use*", "../../use*"], message: "Los hooks de datos se usan en las páginas, no en la UI." },
];
export default tseslint.config(
  { ignores: ["dist", "src/api/schema.d.ts"] },
  ...tseslint.configs.recommended,
  { plugins: { "react-hooks": reactHooks }, rules: { ...reactHooks.configs.recommended.rules } },
  { files: ["src/features/*/ui/**/*.{ts,tsx}", "src/ui/**/*.{ts,tsx}"], rules: { "no-restricted-imports": "off", "@typescript-eslint/no-restricted-imports": ["error", { patterns: forbiddenInUi.map((p) => ({ ...p, allowTypeImports: true })) }] } },
);
