import { useEffect, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { JobFilters, JobStatus, SearchOptions } from "@/api/types";
import { buildSearchView, elapsedSeconds, isFinished } from "@/lib/search";

export const jobsKey = (f: JobFilters) => ["jobs", f] as const;

export function useJobs(filters: JobFilters) {
  return useQuery({ queryKey: jobsKey(filters), queryFn: () => api.jobs(filters), placeholderData: keepPreviousData });
}
export function useJob(id: number) {
  return useQuery({ queryKey: ["job", id], queryFn: () => api.job(id), enabled: Number.isFinite(id) });
}
export function useEvaluation(id: number) {
  return useQuery({ queryKey: ["evaluation", id], queryFn: () => api.evaluation(id), enabled: Number.isFinite(id), staleTime: 60_000 });
}

/** Decisiones del usuario sobre una oferta. Nada se envía solo: «aplicar» abre el destino y registra la intención. */
export function useJobActions(id: number) {
  const qc = useQueryClient();
  const refresh = () => { void qc.invalidateQueries({ queryKey: ["job", id] }); void qc.invalidateQueries({ queryKey: ["jobs"] }); };
  const setStatus = useMutation({ mutationFn: (s: JobStatus) => api.setStatus(id, s), onSuccess: refresh });
  const openApply = useMutation({
    mutationFn: () => api.openApply(id),
    onSuccess: (r) => { if (r.url) window.open(r.url, "_blank", "noopener,noreferrer"); refresh(); },
  });
  return { setStatus, openApply };
}

/** Búsqueda: la lanza, la sigue cada 1,5 s y deja ver el avance por fuente. Si ya hay una en curso (p. ej. tras recargar), se engancha a ella. */
export function useSearch() {
  const qc = useQueryClient();
  const [runId, setRunId] = useState<number | null>(null);
  const [dismissed, setDismissed] = useState<number | null>(null);
  const runs = useQuery({ queryKey: ["search-runs"], queryFn: api.searchRuns, staleTime: 0 });
  useEffect(() => {
    const active = runs.data?.find((r) => !isFinished(r.status));
    if (active && runId === null) setRunId(active.id);
  }, [runs.data, runId]);
  const start = useMutation({ mutationFn: (o: SearchOptions) => api.startSearch(o), onSuccess: (r) => { setDismissed(null); setRunId(r.id); } });
  const run = useQuery({
    queryKey: ["search-run", runId], enabled: runId !== null, queryFn: () => api.searchRun(runId!),
    refetchInterval: (q) => (isFinished(q.state.data?.status) ? false : 1500),
  });
  const finished = run.data ? isFinished(run.data.status) : false;
  useEffect(() => { if (finished) { void qc.invalidateQueries({ queryKey: ["jobs"] }); void qc.invalidateQueries({ queryKey: ["search-runs"] }); } }, [finished, qc]);
  const view = run.data && dismissed !== run.data.id ? buildSearchView(run.data) : null;
  return {
    start: start.mutate, starting: start.isPending, running: runId !== null && !!run.data && !finished,
    view, startedAt: run.data?.created_at, finished, error: start.error ?? run.error,
    dismiss: () => run.data && setDismissed(run.data.id),
  };
}

/** Segundos transcurridos desde `startedAt`, con tic de 1 s mientras `active`. */
export function useElapsed(startedAt: unknown, active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { if (!active) return; const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, [active]);
  return elapsedSeconds(startedAt, now);
}

/** Todas las ofertas (hasta 200) para el panel de métricas. */
export function useAllJobs() {
  return useQuery({ queryKey: ["jobs", "all"], queryFn: () => api.jobs({ limit: 200 }), staleTime: 30_000 });
}
