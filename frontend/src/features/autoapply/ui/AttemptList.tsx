import { ArrowSquareOut, CheckCircle, Hourglass, Warning } from "@phosphor-icons/react";
import type { ApplyAttempt } from "@/api/types";
import { attemptLabel, attemptTone, isActive } from "@/lib/autoapply";
import { Avatar, Badge, Button, Card, Empty } from "@/ui/primitives";

export interface AttemptListProps {
  attempts: readonly ApplyAttempt[];
  busy: boolean;
  onApprove: (id: number) => void;
  onReject: (id: number) => void;
  onRetry: (id: number) => void;
  onOpenJob: (jobId: number) => void;
}
const when = (iso: string) => new Date(iso).toLocaleString("es-CO", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

function Row({ a, busy, onApprove, onReject, onRetry, onOpenJob }: { a: ApplyAttempt } & Omit<AttemptListProps, "attempts">) {
  const unanswered = a.detail.unanswered ?? [];
  return (
    <li className="aa-attempt">
      <Avatar name={a.company ?? a.title ?? "?"} size={38} />
      <div className="aa-attempt-main">
        <button className="aa-link" onClick={() => onOpenJob(a.job_id)}><b>{a.title ?? "Oferta"}</b></button>
        <p className="muted small">{[a.company, a.adapter, when(a.created_at), a.trigger === "auto" ? "automática" : a.trigger === "approved" ? "aprobada por ti" : "pedida por ti"].filter(Boolean).join(" · ")}</p>
        {(a.detail.message || a.error) && <p className="small">{a.detail.message ?? a.error}</p>}
        {unanswered.length > 0 && <ul className="aa-unanswered">{unanswered.slice(0, 5).map((u) => <li key={u}>{u}</li>)}</ul>}
      </div>
      <div className="aa-attempt-side">
        <Badge tone={attemptTone[a.status]}>{isActive(a.status) ? <Hourglass size={12} /> : a.status === "submitted" ? <CheckCircle size={12} weight="fill" /> : a.status === "needs_manual" || a.status === "failed" ? <Warning size={12} /> : null} {attemptLabel[a.status]}</Badge>
        {a.dry_run && a.status !== "awaiting_approval" && <span className="muted small">ensayo</span>}
        <div className="row">
          {a.status === "awaiting_approval" && <><Button busy={busy} onClick={() => onApprove(a.id)}>Aprobar</Button><Button variant="ghost" busy={busy} onClick={() => onReject(a.id)}>Descartar</Button></>}
          {["needs_manual", "unconfirmed", "failed"].includes(a.status) && a.apply_url && <a className="btn btn-secondary" href={a.apply_url} target="_blank" rel="noopener noreferrer"><ArrowSquareOut size={16} />Abrir y terminar yo</a>}
          {["needs_manual", "failed", "dry_run"].includes(a.status) && <Button variant="ghost" busy={busy} onClick={() => onRetry(a.id)}>{a.status === "dry_run" ? "Enviar de verdad" : "Reintentar"}</Button>}
        </div>
      </div>
    </li>
  );
}
/** Cola de aprobación y registro de lo que hizo el sistema, con el porqué de cada resultado. */
export function AttemptList({ attempts, ...h }: AttemptListProps) {
  const waiting = attempts.filter((a) => a.status === "awaiting_approval");
  const rest = attempts.filter((a) => a.status !== "awaiting_approval");
  return (
    <>
      {waiting.length > 0 && <Card title={`Esperan tu aprobación (${waiting.length})`} subtitle="Están listas. No se envía nada hasta que pulses «Aprobar»."><ul className="aa-attempts">{waiting.map((a) => <Row key={a.id} a={a} {...h} />)}</ul></Card>}
      <Card title="Registro" subtitle="Todo lo que el sistema intentó, con su resultado">
        {rest.length === 0 ? <Empty title="Aún no hay intentos" hint="Cuando actives la auto-postulación o pidas «Postular por mí» en una oferta, verás aquí cada paso." /> : <ul className="aa-attempts">{rest.map((a) => <Row key={a.id} a={a} {...h} />)}</ul>}
      </Card>
    </>
  );
}
