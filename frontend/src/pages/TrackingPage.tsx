import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { ChartLineUp, Hourglass, PaperPlaneTilt, Trophy } from "@phosphor-icons/react";
import type { InsightAction } from "@/api/types";
import { useAiStatus } from "@/features/ai/useAiStatus";
import { useApplications, useInsights, useTrackingActions } from "@/features/tracking/useTracking";
import { MetricCard } from "@/features/dashboard/ui/MetricCard";
import { InsightsPanel } from "@/features/tracking/ui/InsightsPanel";
import { TrackingBoard } from "@/features/tracking/ui/TrackingBoard";
import { groupByColumn } from "@/lib/tracking";
import { Empty, ErrorNote, Skeleton } from "@/ui/primitives";

export function TrackingPage() {
  const nav = useNavigate();
  const apps = useApplications();
  const insights = useInsights();
  const ai = useAiStatus();
  const act = useTrackingActions();
  const by = useMemo(() => groupByColumn(apps.data ?? []), [apps.data]);
  const busy = act.markApplied.isPending || act.setStage.isPending || act.setOutcome.isPending;
  const go = (a: InsightAction) => nav(a.target === "profile" ? "/profile" : a.target === "jobs" ? "/jobs" : a.job_id ? `/jobs/${a.job_id}` : "/tracking");
  const sent = (apps.data ?? []).filter((a) => a.applied_at).length;
  return (
    <div className="stack-lg">
      <header className="page-head"><h1>Seguimiento</h1><p className="muted">Qué postulaste, en qué va cada proceso y por qué algunos no avanzan. Tú declaras cada paso: el sistema nunca envía nada por ti.</p></header>
      <section className="metrics" aria-label="Resumen del seguimiento">
        <MetricCard icon={<PaperPlaneTilt size={20} />} label="Postuladas" value={sent} hint={`${by.todo.length} por postular`} tone="good" />
        <MetricCard icon={<Hourglass size={20} />} label="Esperando respuesta" value={by.applied.length} />
        <MetricCard icon={<ChartLineUp size={20} />} label="En proceso" value={by.process.length} hint="Filtro o entrevistas" />
        <MetricCard icon={<Trophy size={20} />} label="Ofertas" value={by.offer.length} tone={by.offer.length ? "good" : "neutral"} />
      </section>
      <ErrorNote error={apps.error ?? act.error} />
      {apps.isLoading ? <Skeleton lines={5} /> : (apps.data?.length ?? 0) === 0 ? (
        <Empty icon={<PaperPlaneTilt size={28} />} title="Aún no sigues ninguna oferta" hint="Abre una oferta y guárdala o márcala como postulada: aparecerá aquí con su estado." />
      ) : (
        <TrackingBoard apps={apps.data!} busy={busy} onOpen={(id) => nav(`/jobs/${id}`)} onMarkApplied={(id) => act.markApplied.mutate(id)} onInterview={(id) => act.setStage.mutate({ jobId: id, stage: "interviewing" })} onOutcome={(id, outcome, reason) => act.setOutcome.mutate({ jobId: id, outcome, reason })} />
      )}
      <InsightsPanel insights={insights.data} loading={insights.isLoading} narrating={insights.narrate && insights.isFetching} aiAvailable={ai.available} onNarrate={insights.askNarrative} onAction={go} onOpenJob={(id) => nav(`/jobs/${id}`)} />
    </div>
  );
}
