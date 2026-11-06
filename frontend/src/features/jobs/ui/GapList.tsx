import type { KeywordGap } from "@/api/types";
import { gapLabel, gapTone } from "@/lib/format";
import { Badge, Card } from "@/ui/primitives";

export interface GapListProps {
  gaps: KeywordGap[];
  confirmed: ReadonlySet<string>;
  onToggle: (keyword: string) => void;
}
/** Lo que pide la oferta frente a lo que el usuario tiene. Solo lo que él confirme puede entrar al CV. */
export function GapList({ gaps, confirmed, onToggle }: GapListProps) {
  return (
    <Card title="Qué pide la oferta">
      <ul className="plain">
        {gaps.map((g) => (
          <li key={g.keyword} className="gap">
            <Badge tone={gapTone[g.status] ?? "neutral"}>{gapLabel[g.status] ?? g.status}</Badge>
            <strong>{g.keyword}</strong>
            {g.evidence && <span className="muted small"> — {g.evidence}</span>}
            {g.status === "missing" && (
              <label className="check"><input type="checkbox" checked={confirmed.has(g.keyword)} onChange={() => onToggle(g.keyword)} />Sí lo tengo (confirmo)</label>
            )}
          </li>
        ))}
      </ul>
      <p className="muted small">Nunca agregamos al CV nada que no tengas: las habilidades que faltan solo entran si tú las confirmas.</p>
    </Card>
  );
}
