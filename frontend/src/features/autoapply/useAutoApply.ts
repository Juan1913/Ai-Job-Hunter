import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { ApplyData, AutoApplyPolicy, CvMode } from "@/api/types";
import { isActive } from "@/lib/autoapply";

export function useCapabilities() {
  return useQuery({ queryKey: ["auto-caps"], queryFn: api.autoCaps, staleTime: 10 * 60_000 });
}
export function usePolicy() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["auto-policy"], queryFn: api.autoPolicy });
  const save = useMutation({ mutationFn: (p: AutoApplyPolicy) => api.savePolicy(p), onSuccess: (p) => { qc.setQueryData(["auto-policy"], p); } });
  return { ...q, save };
}
export function useApplyData() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["auto-data"], queryFn: api.autoData });
  const save = useMutation({ mutationFn: (d: ApplyData) => api.saveData(d), onSuccess: (d) => { qc.setQueryData(["auto-data"], d); void qc.invalidateQueries({ queryKey: ["auto-preview"] }); } });
  return { ...q, save };
}
/** Intentos de postulación; mientras alguno esté en cola o ejecutándose, se refresca cada 2 s. */
export function useAttempts() {
  return useQuery({ queryKey: ["attempts"], queryFn: () => api.attempts(), refetchInterval: (q) => (q.state.data?.some((a) => isActive(a.status)) ? 2000 : false) });
}
export function useAttemptActions() {
  const qc = useQueryClient();
  const refresh = () => { for (const k of ["attempts", "applications", "jobs", "job", "insights", "application"]) void qc.invalidateQueries({ queryKey: [k] }); };
  const approve = useMutation({ mutationFn: (v: { id: number; dryRun?: boolean }) => api.approveAttempt(v.id, v.dryRun), onSuccess: refresh });
  const reject = useMutation({ mutationFn: (id: number) => api.rejectAttempt(id), onSuccess: refresh });
  const retry = useMutation({ mutationFn: (id: number) => api.retryAttempt(id), onSuccess: refresh });
  return { approve, reject, retry, error: approve.error ?? reject.error ?? retry.error };
}
export function useApplyPreview(jobId: number) {
  return useQuery({ queryKey: ["auto-preview", jobId], queryFn: () => api.autoPreview(jobId), enabled: Number.isFinite(jobId) });
}
export function useApplyForMe(jobId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (o: { cv_mode?: CvMode; dry_run?: boolean }) => api.autoApplyNow(jobId, o),
    onSuccess: () => { for (const k of ["attempts", "auto-preview", "applications", "application", "job", "jobs"]) void qc.invalidateQueries({ queryKey: [k] }); },
  });
}
