import { useState } from "react";
import { Lightbulb, MagnifyingGlass, Sparkle, Target, TrendDown, ArrowRight } from "@phosphor-icons/react";
import type { InsightAction, Insights } from "@/api/types";
import { text } from "@/lib/format";
import { confidenceLabel } from "@/lib/tracking";
import { Badge, Button, Card, Empty, Skeleton } from "@/ui/primitives";

export interface InsightsPanelProps {
  insights: Insights | undefined;
  loading: boolean;
  narrating: boolean;
  aiAvailable: boolean;
  onNarrate: () => void;
  onAction: (a: InsightAction) => void;
  onOpenJob: (jobId: number) => void;
}
const KIND_ICON: Record<string, JSX.Element> = { skills: <Target size={18} />, targeting: <MagnifyingGlass size={18} />, cv: <Sparkle size={18} />, ats: <Sparkle size={18} />, followup: <ArrowRight size={18} />, interview: <Lightbulb size={18} /> };
const PRIO = ["", "Prioridad alta", "Prioridad media", "Cuando puedas"];

/** El «por qué» detrás de los rechazos y silencios, y qué cambiar. Siempre como hipótesis con evidencia. */
export function InsightsPanel({ insights, loading, narrating, aiAvailable, onNarrate, onAction, onOpenJob }: InsightsPanelProps) {
  const [showTrace, setShowTrace] = useState(false);
  if (loading && !insights) return <Card title="Qué aprender de tus postulaciones"><Skeleton lines={4} /></Card>;
  if (!insights) return null;
  const i = insights;
  return (
    <Card title="Qué aprender de tus postulaciones" subtitle="Análisis automático de tus candidaturas" actions={aiAvailable && !i.narrative ? <Button variant="secondary" busy={narrating} icon={<Sparkle size={16} />} onClick={onNarrate}>Redactar con IA</Button> : undefined} className="insights">
      <p className="insights-headline">{i.headline}</p>
      {i.narrative && <p className="insights-narrative"><Sparkle size={16} weight="fill" /> {i.narrative}</p>}
      {!i.sample.enough && i.sample.sent > 0 && <p className="muted small">Llevas pocas postulaciones: lo que ves son pistas, no conclusiones.</p>}

      {i.actions.length > 0 && (
        <>
          <h3>Qué cambiar</h3>
          <ul className="actions">
            {i.actions.map((a) => (
              <li key={a.id} className={`action action-p${a.priority}`}>
                <span className="action-icon" aria-hidden>{KIND_ICON[a.kind] ?? <Lightbulb size={18} />}</span>
                <div className="action-body">
                  <div className="row between"><b>{a.title}</b><Badge tone={a.priority === 1 ? "bad" : a.priority === 2 ? "warn" : "neutral"}>{PRIO[a.priority] ?? ""}</Badge></div>
                  <p className="muted">{a.detail}</p>
                  {a.evidence.length > 0 && <p className="muted small">Evidencia: {a.evidence.join(" · ")}</p>}
                </div>
                <Button variant="ghost" icon={<ArrowRight size={16} />} onClick={() => onAction(a)} aria-label={`Ir: ${a.title}`}>Ir</Button>
              </li>
            ))}
          </ul>
        </>
      )}

      {i.diagnoses.length > 0 && (
        <>
          <h3>Por qué no avanzó cada una</h3>
          <ul className="diag">
            {i.diagnoses.map((d) => (
              <li key={d.job_id}>
                <button className="diag-head" onClick={() => onOpenJob(d.job_id)}><TrendDown size={16} /><b>{text(d.title)}</b><span className="muted small">{text(d.company)}</span></button>
                {d.inconclusive ? <p className="muted small">Sin evidencia suficiente: con el motivo que te den (o tu intuición) puedes registrarlo en la candidatura.</p> : (
                  <ul className="causes">{d.causes.map((c) => <li key={c.code}><div className="row"><span>{c.label}</span><Badge tone={c.confidence === "high" ? "good" : c.confidence === "medium" ? "warn" : "neutral"}>{confidenceLabel[c.confidence]}</Badge></div><p className="muted small">{c.evidence}</p></li>)}</ul>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      {i.diagnoses.length === 0 && i.actions.length === 0 && i.sample.sent === 0 && <Empty icon={<Lightbulb size={28} />} title="Aún no hay nada que analizar" hint="Marca como «Ya postulé» tus candidaturas y registra cómo terminan: así detecto patrones y te digo qué mejorar." />}

      <footer className="insights-foot">
        <p className="muted small">{i.caveat}</p>
        <button className="link" onClick={() => setShowTrace((v) => !v)}>{showTrace ? "Ocultar cómo lo analicé" : "Ver cómo lo analicé"}</button>
        {showTrace && <ol className="trace small muted">{i.trace.map((t) => <li key={t}>{t}</li>)}</ol>}
      </footer>
    </Card>
  );
}
