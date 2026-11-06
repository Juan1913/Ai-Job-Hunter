import { useState } from "react";
import { Lightning, ShieldCheck } from "@phosphor-icons/react";
import type { ApplyAttempt, ApplyPreview, CvMode } from "@/api/types";
import { attemptLabel } from "@/lib/autoapply";
import { Badge, Button, Card, ErrorNote } from "@/ui/primitives";

export interface ApplyForMeProps { preview: ApplyPreview | undefined; attempt: ApplyAttempt | undefined; busy: boolean; error: unknown; onApply: (o: { cv_mode: CvMode; dry_run: boolean }) => void }

/** «Postular por mí» para esta oferta: dice qué hará, qué falta y pide confirmación explícita. */
export function ApplyForMePanel({ preview, attempt, busy, error, onApply }: ApplyForMeProps) {
  const [open, setOpen] = useState(false);
  const [cv, setCv] = useState<CvMode>(preview?.cv_mode ?? "tailored");
  const [dry, setDry] = useState(preview?.dry_run ?? true);
  if (!preview) return null;
  return (
    <Card title="Que el sistema postule por mí" subtitle={preview.adapter ? "Rellena el formulario del portal con tus datos y tu CV." : "Este portal no se puede automatizar."} className="aa-apply">
      {attempt && <p className="aa-now">Último intento: <Badge tone={attempt.status === "submitted" ? "good" : attempt.status === "failed" ? "bad" : "neutral"}>{attemptLabel[attempt.status]}</Badge> {attempt.detail.message && <span className="muted small">{attempt.detail.message}</span>}</p>}
      {!preview.can_run ? (
        <ul className="aa-blockers">{preview.blockers.map((b) => <li key={b.code}>{b.message}{b.code === "missing_data" && ` (${preview.missing_data.join(", ")})`}</li>)}</ul>
      ) : !open ? (
        <Button icon={<Lightning size={16} />} onClick={() => setOpen(true)}>Postular por mí</Button>
      ) : (
        <div className="aa-confirm">
          <p><ShieldCheck size={18} /> Revisa y confirma. Solo se postula a esta oferta.</p>
          <fieldset className="aa-cv"><legend>CV</legend>
            <label className={cv === "base" ? "is-on" : ""}><input type="radio" checked={cv === "base"} onChange={() => setCv("base")} /><b>Mi CV general</b></label>
            <label className={cv === "tailored" ? "is-on" : ""}><input type="radio" checked={cv === "tailored"} onChange={() => setCv("tailored")} /><b>Uno hecho para este puesto</b></label>
          </fieldset>
          <label className="check"><input type="checkbox" checked={dry} onChange={(e) => setDry(e.target.checked)} />Solo ensayo: rellenar sin enviar</label>
          <div className="row"><Button busy={busy} onClick={() => onApply({ cv_mode: cv, dry_run: dry })}>{dry ? "Hacer ensayo" : "Confirmo: postular ahora"}</Button><Button variant="ghost" onClick={() => setOpen(false)}>Cancelar</Button></div>
          <p className="muted small">Te quedan {preview.daily_left} hoy. Si el formulario pide algo que no sé, me detengo y te lo dejo para que termines tú.</p>
        </div>
      )}
      <ErrorNote error={error} />
    </Card>
  );
}
