/** Reglas de presentación del seguimiento: en qué columna cae una candidatura y qué decir de ella. Puro. */
import type { Application, Outcome } from "@/api/types";
import type { Tone } from "./format";

export type ColumnId = "todo" | "applied" | "process" | "offer" | "closed";
export const COLUMNS: { id: ColumnId; title: string; hint: string }[] = [
  { id: "todo", title: "Por postular", hint: "Guardadas, aún sin enviar" },
  { id: "applied", title: "Postuladas", hint: "Enviadas, esperando respuesta" },
  { id: "process", title: "En proceso", hint: "Filtro o entrevistas" },
  { id: "offer", title: "Oferta", hint: "Te ofrecieron el puesto" },
  { id: "closed", title: "Cerradas", hint: "Rechazo, sin respuesta o retirada" },
];
export const outcomeLabel: Record<Outcome, string> = { offer: "Oferta recibida", rejected: "Rechazada", no_response: "Sin respuesta", withdrawn: "Retirada por ti" };
export const outcomeTone: Record<Outcome, Tone> = { offer: "good", rejected: "bad", no_response: "warn", withdrawn: "neutral" };

export function columnOf(a: Application): ColumnId {
  if (a.outcome === "offer" || a.stage === "offer") return "offer";
  if (a.outcome || a.stage === "closed") return "closed";
  if (a.stage === "screening" || a.stage === "interviewing") return "process";
  return a.applied_at ? "applied" : "todo";
}
export function groupByColumn(apps: readonly Application[]): Record<ColumnId, Application[]> {
  const out: Record<ColumnId, Application[]> = { todo: [], applied: [], process: [], offer: [], closed: [] };
  for (const a of apps) out[columnOf(a)].push(a);
  return out;
}
export function daysSince(iso: string | null | undefined, now = Date.now()): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : Math.max(0, Math.floor((now - t) / 86_400_000));
}
/** El estado que más importa: ¿ya postulaste o no? */
export function appliedState(a: { applied_at: string | null; outcome: Outcome | null }, now = Date.now()): { applied: boolean; label: string; tone: Tone } {
  if (!a.applied_at) return { applied: false, label: "Aún no postulaste", tone: "warn" };
  const d = daysSince(a.applied_at, now);
  return { applied: true, label: `Postulaste ${d === 0 ? "hoy" : d === 1 ? "ayer" : `hace ${d} días`}`, tone: "good" };
}
export const confidenceLabel = { high: "Respaldo alto", medium: "Respaldo medio", low: "Hipótesis débil" } as const;
