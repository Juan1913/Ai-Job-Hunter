import { useSchedules } from "@/features/schedules/useSchedules";
import { ScheduleForm } from "@/features/schedules/ui/ScheduleForm";
import { ScheduleList } from "@/features/schedules/ui/ScheduleList";
import { WorkerBanner } from "@/features/schedules/ui/WorkerBanner";
import { ErrorNote, Skeleton } from "@/ui/primitives";

export function SchedulesPage() {
  const s = useSchedules();
  const busy = s.pause.isPending || s.resume.isPending || s.run.isPending || s.remove.isPending;
  return (
    <div className="stack-lg">
      <header className="page-head"><h1>Búsquedas programadas</h1><p className="muted">Que el sistema busque solo cada cierto tiempo, con tus filtros, y te avise (o postule, si lo activaste) cuando aparezca algo nuevo.</p></header>
      <WorkerBanner alive={s.worker.data?.alive} loading={s.worker.isLoading} />
      <ErrorNote error={s.list.error ?? s.error} />
      <div className="pt-layout">
        <ScheduleForm limits={s.limits.data} busy={s.create.isPending} error={s.create.error} onCreate={(b) => s.create.mutate(b)} />
        {s.list.isLoading ? <Skeleton lines={5} /> : <ScheduleList items={s.list.data ?? []} busy={busy} onRun={(id) => s.run.mutate(id)} onPause={(id) => s.pause.mutate(id)} onResume={(id) => s.resume.mutate(id)} onDelete={(id) => s.remove.mutate(id)} />}
      </div>
    </div>
  );
}
