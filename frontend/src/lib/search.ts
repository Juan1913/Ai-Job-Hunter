/** Modelo de vista de una búsqueda en curso: qué fuentes se recorren, cuánto va y qué se ha encontrado. Puro. */
import type { SearchRun } from "@/api/types";
import { sourceName } from "./format";

export interface SampleJob { title: string; company: string; location: string; remote: boolean }
export interface SourceRow {
  key: string;
  name: string;
  state: "pending" | "done" | "empty" | "blocked" | "error";
  found: number;
  seconds: number | null;
  error: string | null;
  sample: SampleJob[];
}
export interface SearchView {
  phase: "queued" | "scanning" | "saving" | "done" | "failed";
  label: string;
  progress: number;           // 0-100
  rows: SourceRow[];
  found: number;
  doneSources: number;
  totalSources: number;
  feed: (SampleJob & { source: string })[];
  newJobs: number;
  keywords: string[];
  error: string | null;
}

export const isFinished = (status: string | undefined): boolean => ["done", "failed"].includes(String(status));

function rowOf(key: string, v: Record<string, unknown>): SourceRow {
  const status = String(v.status ?? "pending");
  const outcome = String(v.outcome ?? "");
  const found = Number(v.found ?? 0);
  const state: SourceRow["state"] = status === "pending" ? "pending" : outcome === "blocked" ? "blocked" : status === "error" ? "error" : found === 0 ? "empty" : "done";
  return { key, name: sourceName(key), state, found, seconds: typeof v.seconds === "number" ? v.seconds : null, error: v.error ? String(v.error) : null, sample: Array.isArray(v.sample) ? (v.sample as SampleJob[]) : [] };
}

export function buildSearchView(run: SearchRun): SearchView {
  const raw = (run.sources && typeof run.sources === "object" ? run.sources : {}) as Record<string, Record<string, unknown>>;
  const rows = Object.entries(raw).map(([k, v]) => rowOf(k, v ?? {}));
  const doneSources = rows.filter((r) => r.state !== "pending").length;
  const status = String(run.status);
  const failed = status === "failed";
  const finished = status === "done";
  const phase: SearchView["phase"] = failed ? "failed" : finished ? "done" : status === "queued" || rows.length === 0 ? "queued" : doneSources >= rows.length ? "saving" : "scanning";
  const label = { queued: "En cola, empezando…", scanning: `Recorriendo ${rows.length} fuentes`, saving: "Puntuando y guardando lo encontrado", done: "Búsqueda terminada", failed: "La búsqueda falló" }[phase];
  const progress = finished ? 100 : rows.length ? Math.round((doneSources / rows.length) * 90) : 4;
  const feed = rows.flatMap((r) => r.sample.map((s) => ({ ...s, source: r.name }))).slice(0, 12);
  return { phase, label, progress, rows, found: rows.reduce((a, r) => a + r.found, 0), doneSources, totalSources: rows.length, feed, newJobs: Number(run.new_jobs ?? 0), keywords: Array.isArray(run.keywords) ? (run.keywords as string[]) : [], error: run.error ? String(run.error) : null };
}

/** Segundos desde `startedAt` (ISO; el servidor puede mandarlo sin zona: se asume UTC). */
export function elapsedSeconds(startedAt: unknown, now = Date.now()): number {
  if (typeof startedAt !== "string") return 0;
  const t = Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(startedAt) ? startedAt : `${startedAt}Z`);
  return Number.isNaN(t) ? 0 : Math.max(0, Math.round((now - t) / 1000));
}
export const clock = (s: number): string => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

/** Color estable por nombre (para el avatar de una empresa). */
export function hueOf(name: string): number {
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 360;
  return h;
}
