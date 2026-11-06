import type { Evaluation, Recommendation } from "@/api/types";
import { recommendationLabel, recommendationTone, scoreTone } from "@/lib/format";
import { Badge, Card, ScoreRing } from "@/ui/primitives";

/** Resumen lateral del encaje: el número, la recomendación y los tres motivos principales. */
export function ScoreSummary({ evaluation }: { evaluation: Evaluation }) {
  const rec = evaluation.recommendation as Recommendation;
  return (
    <Card title="Encaje con tu perfil" className="score-card-wrap">
      <div className="score-card">
        <ScoreRing value={(evaluation.score / 5) * 100} label={evaluation.score.toFixed(1)} tone={scoreTone(evaluation.score)} size={96} />
        <Badge tone={recommendationTone[rec]}>{recommendationLabel[rec]}</Badge>
      </div>
      {evaluation.vetoes?.map((v) => <p key={v.code} className="error">{v.reason}</p>)}
      {evaluation.reasons_top3 && evaluation.reasons_top3.length > 0 && <ul className="plain" style={{ marginTop: 14 }}>{evaluation.reasons_top3.map((r) => <li key={r} className="muted small">{r}</li>)}</ul>}
    </Card>
  );
}
