import { useState, type FormEvent } from "react";
import type { Outcome } from "@/api/types";
import { outcomeLabel } from "@/lib/tracking";
import { Button } from "@/ui/primitives";

const OPTIONS: Outcome[] = ["rejected", "no_response", "offer", "withdrawn"];
/** Registrar cómo terminó. El motivo es opcional, pero es lo que más ayuda al análisis. */
export function OutcomeForm({ busy, onSubmit, onCancel }: { busy: boolean; onSubmit: (o: Outcome, reason: string) => void; onCancel: () => void }) {
  const [outcome, setOutcome] = useState<Outcome>("rejected");
  const [reason, setReason] = useState("");
  const submit = (e: FormEvent) => { e.preventDefault(); onSubmit(outcome, reason.trim()); };
  return (
    <form className="outcome" onSubmit={submit} onClick={(e) => e.stopPropagation()}>
      <div className="seg" role="group" aria-label="Resultado">
        {OPTIONS.map((o) => <button key={o} type="button" className={`seg-item ${o === outcome ? "is-active" : ""}`} aria-pressed={o === outcome} onClick={() => setOutcome(o)}>{outcomeLabel[o]}</button>)}
      </div>
      <label>Motivo, si te lo dijeron o lo sabes<textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Ej.: buscaban más años de experiencia en Kubernetes" /></label>
      <div className="row"><Button type="submit" busy={busy}>Guardar resultado</Button><Button type="button" variant="ghost" onClick={onCancel}>Cancelar</Button></div>
    </form>
  );
}
