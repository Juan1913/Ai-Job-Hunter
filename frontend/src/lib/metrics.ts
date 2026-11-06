/** Métricas del panel, calculadas en el cliente a partir de las ofertas ya cargadas. Funciones puras. */
import type { JobOut } from "@/api/types";
import { sourceName } from "./format";

export interface Metrics {
  total: number;
  fresh: number;              // llegaron en los últimos 7 días
  worthApplying: number;      // recomendación «postular»
  avgScore: number | null;    // 1.0-5.0, solo las evaluadas
  evaluated: number;
  pipeline: { saved: number; applied: number; interview: number; offer: number };
  scoreBuckets: { label: string; count: number }[];
  topSources: { name: string; count: number }[];
}

const BUCKETS = [["< 2", 0, 2], ["2-3", 2, 3], ["3-4", 3, 4], ["4-5", 4, 5.01]] as const;

export function computeMetrics(items: readonly JobOut[], now = new Date()): Metrics {
  const scores = items.map((i) => i.evaluation?.score).filter((s): s is number => typeof s === "number");
  const weekAgo = now.getTime() - 7 * 86_400_000;
  const bySource = new Map<string, number>();
  for (const i of items) bySource.set(i.job.source.split(":")[0]!, (bySource.get(i.job.source.split(":")[0]!) ?? 0) + 1);
  const count = (st: string) => items.filter((i) => i.status === st).length;
  return {
    total: items.length,
    fresh: items.filter((i) => { const t = Date.parse(i.first_seen); return !Number.isNaN(t) && t >= weekAgo; }).length,
    worthApplying: items.filter((i) => i.evaluation?.recommendation === "apply").length,
    avgScore: scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null,
    evaluated: scores.length,
    pipeline: { saved: count("saved"), applied: count("applied"), interview: count("interview"), offer: count("offer") },
    scoreBuckets: BUCKETS.map(([label, lo, hi]) => ({ label, count: scores.filter((s) => s >= lo && s < hi).length })),
    topSources: [...bySource.entries()].map(([name, n]) => ({ name: sourceName(name), count: n })).sort((a, b) => b.count - a.count).slice(0, 5),
  };
}

/** Mejores coincidencias para el panel: primero las evaluadas con más puntaje, sin descartadas/postuladas. */
export function topMatches(items: readonly JobOut[], n = 6): JobOut[] {
  return items
    .filter((i) => !["discarded", "rejected", "applied"].includes(i.status) && !i.job.closed)
    .sort((a, b) => (b.evaluation?.score ?? (b.match ?? 0) / 25) - (a.evaluation?.score ?? (a.match ?? 0) / 25))
    .slice(0, n);
}

/** Ofertas nuevas por día durante los últimos `days` días (más antiguo primero), para la línea de tendencia. */
export function perDay(items: readonly JobOut[], days = 14, now = new Date()): number[] {
  const out = Array<number>(days).fill(0);
  const end = new Date(now); end.setHours(23, 59, 59, 999);
  for (const i of items) {
    const t = Date.parse(i.first_seen);
    if (Number.isNaN(t)) continue;
    const back = Math.floor((end.getTime() - t) / 86_400_000);
    if (back >= 0 && back < days) out[days - 1 - back]!++;
  }
  return out;
}
