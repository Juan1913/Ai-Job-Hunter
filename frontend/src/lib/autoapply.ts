/** Reglas de presentación de la auto-postulación. Puro. */
import type { ApplyMode, AttemptStatus } from "@/api/types";
import type { Tone } from "./format";

export const modeInfo: Record<ApplyMode, { label: string; hint: string }> = {
  off: { label: "Apagada", hint: "No hace nada. Tú postulas con tu botón de siempre." },
  approval: { label: "Con tu aprobación", hint: "Prepara todo y te avisa. Postula solo cuando tú pulsas «Aprobar»." },
  auto: { label: "Automática", hint: "Postula sola a lo que cumpla tus reglas, apenas aparezca la oferta." },
};
export const attemptLabel: Record<AttemptStatus, string> = {
  awaiting_approval: "Esperando tu aprobación", queued: "En cola", running: "Postulando…", submitted: "Postulada", dry_run: "Ensayo (no se envió)",
  needs_manual: "Termina tú", unconfirmed: "Verifica si se envió", failed: "Falló", rejected: "Descartada por ti",
};
export const attemptTone: Record<AttemptStatus, Tone> = {
  awaiting_approval: "warn", queued: "neutral", running: "neutral", submitted: "good", dry_run: "neutral", needs_manual: "warn", unconfirmed: "warn", failed: "bad", rejected: "neutral",
};
export const isActive = (s: AttemptStatus) => s === "queued" || s === "running";
export const kindLabel: Record<string, string> = {
  greenhouse: "Greenhouse", lever: "Lever", ashby: "Ashby", workable: "Workable", smartrecruiters: "SmartRecruiters", workday: "Workday", teamtailor: "Teamtailor",
  bamboohr: "BambooHR", recruitee: "Recruitee", careers: "Página de empleo", successfactors: "SuccessFactors",
};
export const portalKindLabel = (k: string): string => kindLabel[k] ?? k;
export const canAutomate = (kind: string, automatable: readonly string[]): boolean => automatable.includes(kind);
/** Resumen en una frase de la política, para mostrarlo arriba. */
export function policySentence(p: { mode: ApplyMode; paused: boolean; dry_run: boolean; min_score: number; daily_cap: number; cv_mode: string }): string {
  if (p.mode === "off") return "Apagada: el sistema no postula a nada.";
  if (p.paused) return "En pausa: no se prepara ni se envía nada hasta que la reanudes.";
  const what = p.mode === "auto" ? "Postula sola" : "Prepara y espera tu aprobación para postular";
  const how = p.dry_run ? " en MODO ENSAYO (rellena el formulario pero no lo envía)" : "";
  return `${what}${how} a ofertas con encaje ≥ ${p.min_score.toFixed(1)}, hasta ${p.daily_cap} al día, con ${p.cv_mode === "tailored" ? "un CV hecho para cada puesto" : "tu CV general"}.`;
}
