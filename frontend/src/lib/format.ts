/** Funciones puras de presentación (sin React, sin red): se prueban solas y las usan hooks y UI por igual. */
import type { Job, JobStatus, Recommendation } from "@/api/types";

export type Tone = "good" | "warn" | "bad" | "neutral";

export const recommendationLabel: Record<Recommendation, string> = { apply: "Vale la pena postular", consider: "Considerar", skip: "Descartar" };
export const recommendationTone: Record<Recommendation, Tone> = { apply: "good", consider: "warn", skip: "bad" };
export const statusLabel: Record<JobStatus, string> = { new: "Nueva", saved: "Guardada", discarded: "Descartada", applied: "Postulada", interview: "Entrevista", offer: "Oferta", rejected: "Rechazada" };
export const gapLabel: Record<string, string> = { have: "Lo tienes", adjacent: "Cercano (reescribible)", missing: "No lo tienes" };
export const gapTone: Record<string, Tone> = { have: "good", adjacent: "warn", missing: "bad" };

/** Puntaje 1.0-5.0 de la evaluación → tono. */
export function scoreTone(score: number | null | undefined): Tone {
  if (score == null) return "neutral";
  return score >= 3.8 ? "good" : score >= 3.0 ? "warn" : "bad";
}
export const atsTone = (s: number): Tone => (s >= 85 ? "good" : s >= 70 ? "warn" : "bad");

export function formatSalary(j: Pick<Job, "salary" | "salary_min" | "salary_max" | "salary_currency" | "salary_period">): string | null {
  if (j.salary_min != null || j.salary_max != null) {
    const n = (v: number) => new Intl.NumberFormat("es-CO", { maximumFractionDigits: 0 }).format(v);
    const range = j.salary_min != null && j.salary_max != null && j.salary_min !== j.salary_max ? `${n(j.salary_min)} – ${n(j.salary_max)}` : n((j.salary_min ?? j.salary_max)!);
    return [range, j.salary_currency, j.salary_period ? `/ ${j.salary_period}` : ""].filter(Boolean).join(" ");
  }
  return j.salary ?? null;
}

export function sourceName(source: string): string {
  const [kind, tenant] = source.split(":");
  const base = kind ? kind.charAt(0).toUpperCase() + kind.slice(1) : source;
  return tenant ? `${base} · ${tenant}` : base;
}

export function relativeDays(iso: string | null | undefined, now = new Date()): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const days = Math.floor((now.getTime() - d.getTime()) / 86_400_000);
  return days <= 0 ? "hoy" : days === 1 ? "ayer" : `hace ${days} días`;
}

export const initialsOf = (name: string | null | undefined): string =>
  (name ?? "").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("") || "?";

/** Algunas fuentes entregan texto con entidades HTML («&amp;»): se muestran como el texto que son. */
export function text(s: string | null | undefined): string {
  return (s ?? "").replace(/&(amp|lt|gt|quot|#39|apos|nbsp);/g, (_, e: string) => ({ amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'", apos: "'", nbsp: " " })[e] ?? "");
}
