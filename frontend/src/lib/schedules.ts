/** Presentación de búsquedas programadas. Puro. */
import type { SavedSearch } from "@/api/types";

export const INTERVALS: { minutes: number; label: string }[] = [
  { minutes: 60, label: "Cada hora" }, { minutes: 180, label: "Cada 3 horas" }, { minutes: 360, label: "Cada 6 horas" },
  { minutes: 720, label: "Cada 12 horas" }, { minutes: 1440, label: "Cada día" },
];
export function intervalLabel(min: number): string {
  const hit = INTERVALS.find((i) => i.minutes === min);
  if (hit) return hit.label.toLowerCase();
  return min % 1440 === 0 ? `cada ${min / 1440} días` : min % 60 === 0 ? `cada ${min / 60} horas` : `cada ${min} min`;
}
/** «en 2 h», «hace 15 min», «ahora»: relativo a `now`. */
export function whenLabel(iso: string | null, now = Date.now()): string {
  if (!iso) return "sin programar";
  const diff = Date.parse(iso) - now;
  if (Number.isNaN(diff)) return "sin programar";
  const abs = Math.abs(diff), m = Math.round(abs / 60000);
  if (m < 1) return "ahora";
  const txt = m < 60 ? `${m} min` : m < 1440 ? `${Math.round(m / 60)} h` : `${Math.round(m / 1440)} d`;
  return diff > 0 ? `en ${txt}` : `hace ${txt}`;
}
const MODE = { remote: "Remoto", hybrid: "Híbrido", onsite: "Presencial" } as const;
const CONTRACT: Record<string, string> = { full_time: "Tiempo completo", part_time: "Medio tiempo", contract: "Por contrato", internship: "Prácticas", temporary: "Temporal" };
/** Etiquetas cortas con los filtros que tiene la búsqueda. */
export function paramChips(p: SavedSearch["params"]): string[] {
  const out: string[] = [];
  for (const m of p.work_modes ?? []) out.push(MODE[m] ?? m);
  for (const c of p.countries ?? []) out.push(c);
  for (const t of p.employment_types ?? []) out.push(CONTRACT[t] ?? t);
  if (p.hours_old) out.push(p.hours_old <= 24 ? "Últimas 24 h" : p.hours_old <= 72 ? "Últimos 3 días" : p.hours_old <= 168 ? "Última semana" : "Último mes");
  return out;
}
export const pausedWhy = (s: Pick<SavedSearch, "paused_reason" | "last_error">): string =>
  s.paused_reason === "failures" ? `Se pausó sola tras varios fallos seguidos${s.last_error ? `: ${s.last_error}` : ""}` : s.paused_reason ? `Pausada (${s.paused_reason})` : "Pausada por ti";
