import { useCallback, useEffect, useState } from "react";

export type Theme = "dark" | "light";
const KEY = "jh.theme";
const read = (): Theme => {
  try { const v = localStorage.getItem(KEY); if (v === "light" || v === "dark") return v; } catch { /* sin almacenamiento */ }
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
};
/** Tema oscuro por defecto; se recuerda la elección. */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>(read);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem(KEY, theme); } catch { /* sin almacenamiento */ }
  }, [theme]);
  const toggle = useCallback(() => setTheme((t) => (t === "dark" ? "light" : "dark")), []);
  return { theme, toggle };
}
