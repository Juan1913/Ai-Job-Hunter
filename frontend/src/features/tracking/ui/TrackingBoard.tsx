import type { Application, Outcome } from "@/api/types";
import { COLUMNS, groupByColumn } from "@/lib/tracking";
import { ApplicationCard } from "./ApplicationCard";

export interface TrackingBoardProps {
  apps: readonly Application[];
  busy: boolean;
  onOpen: (jobId: number) => void;
  onMarkApplied: (jobId: number) => void;
  onInterview: (jobId: number) => void;
  onOutcome: (jobId: number, outcome: Outcome | null, reason: string) => void;
}
/** Tablero por etapas: de «Por postular» a «Cerradas». */
export function TrackingBoard({ apps, ...rest }: TrackingBoardProps) {
  const by = groupByColumn(apps);
  return (
    <div className="board" role="list">
      {COLUMNS.map((c) => (
        <section key={c.id} className="col" role="listitem" aria-label={c.title}>
          <header className="col-head"><h3>{c.title}</h3><span className="count">{by[c.id].length}</span></header>
          <p className="muted small col-hint">{c.hint}</p>
          <div className="col-body">
            {by[c.id].length === 0 ? <p className="col-empty muted small">Nada aquí</p> : by[c.id].map((a) => <ApplicationCard key={a.id} app={a} {...rest} />)}
          </div>
        </section>
      ))}
    </div>
  );
}
