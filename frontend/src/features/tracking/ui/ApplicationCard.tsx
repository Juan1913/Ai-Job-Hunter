import { useState } from "react";
import { CalendarBlank, CheckCircle, ClockCountdown, PaperPlaneTilt, Phone } from "@phosphor-icons/react";
import type { Application, Outcome } from "@/api/types";
import { text } from "@/lib/format";
import { appliedState, outcomeLabel, outcomeTone } from "@/lib/tracking";
import { Avatar, Badge, Button } from "@/ui/primitives";
import { OutcomeForm } from "./OutcomeForm";

export interface ApplicationCardProps {
  app: Application;
  busy: boolean;
  onOpen: (jobId: number) => void;
  onMarkApplied: (jobId: number) => void;
  onInterview: (jobId: number) => void;
  onOutcome: (jobId: number, outcome: Outcome | null, reason: string) => void;
}
const date = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString("es-CO", { day: "numeric", month: "short" }) : null);

/** Una candidatura: lo primero que dice es si ya postulaste o no, y ofrece solo las acciones que tocan en su etapa. */
export function ApplicationCard({ app, busy, onOpen, onMarkApplied, onInterview, onOutcome }: ApplicationCardProps) {
  const [closing, setClosing] = useState(false);
  const st = appliedState(app);
  const done = !!app.outcome;
  const reminder = app.reminders[0];
  return (
    <article className="tcard" onClick={() => onOpen(app.job_id)}>
      <header className="tcard-top"><Avatar name={app.company ?? app.title ?? "?"} size={34} /><div className="tcard-who"><p className="tcard-title">{text(app.title) || "Oferta"}</p><p className="muted small">{text(app.company)}</p></div></header>
      <div className="tcard-state">
        {done ? <Badge tone={outcomeTone[app.outcome!]}>{outcomeLabel[app.outcome!]}</Badge> : <span className={`applied applied-${st.applied ? "yes" : "no"}`}>{st.applied ? <CheckCircle size={15} weight="fill" /> : <PaperPlaneTilt size={15} />}{st.label}</span>}
        {app.interviews.length > 0 && <Badge><Phone size={12} /> {app.interviews.length} {app.interviews.length === 1 ? "entrevista" : "entrevistas"}</Badge>}
      </div>
      {app.outcome_reason && <p className="muted small tcard-reason">«{app.outcome_reason}»</p>}
      {(app.deadline_at || app.next_step) && !done && <p className="muted small tcard-line">{app.deadline_at ? <><ClockCountdown size={14} /> Vence {date(app.deadline_at)}</> : <><CalendarBlank size={14} /> {app.next_step}</>}</p>}
      {reminder && !done && <p className="tcard-reminder small">{String(reminder.message ?? reminder.kind)}</p>}
      {closing ? <OutcomeForm busy={busy} onSubmit={(o, r) => { onOutcome(app.job_id, o, r); setClosing(false); }} onCancel={() => setClosing(false)} /> : (
        <footer className="tcard-actions" onClick={(e) => e.stopPropagation()}>
          {done ? <Button variant="ghost" busy={busy} onClick={() => onOutcome(app.job_id, null, "")}>Reabrir</Button>
            : !app.applied_at ? <Button busy={busy} icon={<PaperPlaneTilt size={16} />} onClick={() => onMarkApplied(app.job_id)}>Ya postulé</Button>
            : <>
              {app.stage !== "interviewing" && <Button variant="secondary" busy={busy} onClick={() => onInterview(app.job_id)}>Pasé a entrevista</Button>}
              <Button variant="ghost" onClick={() => setClosing(true)}>Registrar resultado</Button>
            </>}
        </footer>
      )}
    </article>
  );
}
