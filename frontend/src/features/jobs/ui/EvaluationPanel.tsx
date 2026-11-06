import type { Evaluation } from "@/api/types";
import { recommendationLabel, recommendationTone } from "@/lib/format";
import type { Recommendation } from "@/api/types";
import { Badge, Card, Meter } from "@/ui/primitives";

export function EvaluationPanel({ evaluation }: { evaluation: Evaluation }) {
  const rec = evaluation.recommendation as Recommendation;
  return (
    <Card title="Evaluación" actions={<Badge tone={recommendationTone[rec]}>{recommendationLabel[rec]}</Badge>}>
      {evaluation.vetoes?.map((v) => <p key={v.code} className="error">{v.reason}</p>)}
      {evaluation.flags?.map((f) => <p key={f.code} className={f.severity === "alert" ? "error" : "muted"}>{f.message}</p>)}
      <h3>Por dimensión</h3>
      {evaluation.dimensions.map((d) => (
        <div key={d.key} className="dim">
          <div className="row between"><span>{d.label}</span><span className="muted">{d.status === "neutral" ? "sin dato" : `${Math.round(d.score * 100)}%`}</span></div>
          <Meter value={d.score * 100} tone={d.status === "neutral" ? "neutral" : d.score >= 0.7 ? "good" : d.score >= 0.4 ? "warn" : "bad"} label={d.label} />
          <p className="muted small">{d.explanation}</p>
        </div>
      ))}
    </Card>
  );
}
