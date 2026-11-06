import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";

export function useSchedules() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["schedules"], queryFn: api.schedules, refetchInterval: 30_000 });
  const limits = useQuery({ queryKey: ["schedule-limits"], queryFn: api.scheduleLimits, staleTime: 10 * 60_000 });
  const worker = useQuery({ queryKey: ["worker-status"], queryFn: api.workerStatus, refetchInterval: 15_000 });
  const refresh = () => { void qc.invalidateQueries({ queryKey: ["schedules"] }); };
  const create = useMutation({ mutationFn: api.createSchedule, onSuccess: refresh });
  const remove = useMutation({ mutationFn: api.deleteSchedule, onSuccess: refresh });
  const pause = useMutation({ mutationFn: api.pauseSchedule, onSuccess: refresh });
  const resume = useMutation({ mutationFn: api.resumeSchedule, onSuccess: refresh });
  const run = useMutation({ mutationFn: api.runSchedule, onSuccess: () => { refresh(); void qc.invalidateQueries({ queryKey: ["search-runs"] }); } });
  return { list, limits, worker, create, remove, pause, resume, run, error: create.error ?? remove.error ?? pause.error ?? resume.error ?? run.error };
}
