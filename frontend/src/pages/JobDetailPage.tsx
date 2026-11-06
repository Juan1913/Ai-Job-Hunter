import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import type { CvTemplate } from "@/api/types";
import { useEvaluation, useJob, useJobActions } from "@/features/jobs/useJobs";
import { useApplication, useTrackingActions } from "@/features/tracking/useTracking";
import { appliedState } from "@/lib/tracking";
import { useApplyForMe, useApplyPreview, useAttempts } from "@/features/autoapply/useAutoApply";
import { ApplyForMePanel } from "@/features/autoapply/ui/ApplyForMePanel";
import { useCvs, useDownloadCv, useTailor } from "@/features/cv/useTailor";
import { EvaluationPanel } from "@/features/jobs/ui/EvaluationPanel";
import { GapList } from "@/features/jobs/ui/GapList";
import { TailoredPreview } from "@/features/cv/ui/TailoredPreview";
import { DownloadBar } from "@/features/cv/ui/DownloadBar";
import { JobHero } from "@/features/jobs/ui/JobHero";
import { ScoreSummary } from "@/features/jobs/ui/ScoreSummary";
import { Button, Card, ErrorNote, Skeleton, Spinner } from "@/ui/primitives";

export function JobDetailPage() {
  const id = Number(useParams().id);
  const job = useJob(id);
  const evaluation = useEvaluation(id);
  const cvs = useCvs(id);
  const tailor = useTailor(id);
  const download = useDownloadCv();
  const { setStatus, openApply } = useJobActions(id);
  const application = useApplication(id);
  const track = useTrackingActions();
  const preview = useApplyPreview(id);
  const attempts = useAttempts();
  const applyForMe = useApplyForMe(id);
  const lastAttempt = attempts.data?.find((a) => a.job_id === id);
  const [confirmed, setConfirmed] = useState<ReadonlySet<string>>(new Set());
  const [template, setTemplate] = useState<CvTemplate>("harvard");

  if (job.isLoading) return <Skeleton lines={5} />;
  if (!job.data) return <ErrorNote error={job.error ?? new Error("Oferta no encontrada")} />;
  const { job: j, gaps } = job.data;
  const latest = tailor.data ?? cvs.data?.[0];
  const toggle = (k: string) => setConfirmed((s) => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n; });

  const st = appliedState(application.data ?? { applied_at: null, outcome: null });
  return (
    <div className="stack-lg">
      <Link className="back" to="/jobs">← Volver a ofertas</Link>
      <div className="detail">
        <div className="detail-main">
          <JobHero job={j} applied={st.applied} appliedLabel={`${st.label}${application.data?.outcome ? ` · ${application.data.outcome}` : ""}`}
            actions={<>
              <Button busy={openApply.isPending} onClick={() => openApply.mutate()}>Ir a postular ↗</Button>
              <Button variant="secondary" busy={setStatus.isPending} onClick={() => setStatus.mutate("saved")}>Guardar</Button>
              <Button variant="secondary" busy={track.markApplied.isPending} disabled={!!application.data?.applied_at} onClick={() => track.markApplied.mutate(id)}>{application.data?.applied_at ? "Ya postulaste" : "Ya postulé"}</Button>
              <Button variant="ghost" busy={setStatus.isPending} onClick={() => setStatus.mutate("discarded")}>Descartar</Button>
            </>}
            footer={<>
              <p className="muted small">«Ir a postular» abre la oferta original y postulas tú. Si prefieres, abajo puedes pedir que el sistema postule por ti, con tu confirmación.</p>
              <ErrorNote error={openApply.error ?? setStatus.error ?? track.error} />
              <details><summary>Descripción de la oferta</summary><pre className="cvtext">{j.description}</pre></details>
            </>} />
          {evaluation.isLoading ? <Spinner label="Evaluando…" /> : evaluation.data ? <EvaluationPanel evaluation={evaluation.data} /> : <ErrorNote error={evaluation.error} />}
          {gaps && gaps.length > 0 && <GapList gaps={gaps} confirmed={confirmed} onToggle={toggle} />}
        </div>
        <aside className="detail-side">
          {evaluation.data && <ScoreSummary evaluation={evaluation.data} />}
          <ApplyForMePanel key={preview.data ? "ready" : "loading"} preview={preview.data} attempt={lastAttempt} busy={applyForMe.isPending} error={applyForMe.error} onApply={(o) => applyForMe.mutate(o)} />
          <Card title="Tu CV para esta oferta" actions={<Button busy={tailor.isPending} onClick={() => tailor.mutate([...confirmed])}>{latest ? "Regenerar" : "Generar CV"}</Button>}>
            <ErrorNote error={tailor.error ?? download.error} />
            {!latest && !tailor.isPending && <p className="muted small">Genera un CV ajustado a lo que pide la oferta, sin inventar nada. Pasa los filtros ATS y tú decides si lo usas.</p>}
            {tailor.isPending && <Spinner label="Redactando tu CV…" />}
            {latest && <div className="stack"><DownloadBar template={template} busy={download.isPending} onTemplate={setTemplate} onDownload={(format) => download.mutate({ cvId: latest.id, format, template })} /></div>}
          </Card>
        </aside>
      </div>
      {latest && <TailoredPreview cv={latest.cv} />}
    </div>
  );
}
