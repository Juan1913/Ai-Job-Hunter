import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { Outcome, Stage } from "@/api/types";

export function useApplications() {
  return useQuery({ queryKey: ["applications"], queryFn: api.applications });
}
export function useApplication(jobId: number) {
  return useQuery({ queryKey: ["application", jobId], queryFn: () => api.application(jobId), enabled: Number.isFinite(jobId) });
}
/** Análisis del seguimiento. `narrate` pide además la nota redactada por IA (solo si el modo de IA lo permite). */
export function useInsights() {
  const [narrate, setNarrate] = useState(false);
  const q = useQuery({ queryKey: ["insights", narrate], queryFn: () => api.insights(narrate), staleTime: 30_000 });
  return { ...q, narrate, askNarrative: () => setNarrate(true) };
}
/** Acciones del usuario sobre una candidatura. Todo lo declara él: el sistema nunca envía nada. */
export function useTrackingActions() {
  const qc = useQueryClient();
  const refresh = () => { for (const k of ["applications", "application", "insights", "jobs", "job"]) void qc.invalidateQueries({ queryKey: [k] }); };
  const markApplied = useMutation({ mutationFn: (jobId: number) => api.markApplied(jobId), onSuccess: refresh });
  const track = useMutation({ mutationFn: (jobId: number) => api.startTracking(jobId), onSuccess: refresh });
  const setStage = useMutation({ mutationFn: (v: { jobId: number; stage: Stage }) => api.setStage(v.jobId, v.stage), onSuccess: refresh });
  const setOutcome = useMutation({ mutationFn: (v: { jobId: number; outcome: Outcome | null; reason?: string }) => api.setOutcome(v.jobId, v.outcome, v.reason), onSuccess: refresh });
  return { markApplied, track, setStage, setOutcome, error: markApplied.error ?? track.error ?? setStage.error ?? setOutcome.error };
}
