import type { TailoredCV } from "@/api/types";
import { atsTone } from "@/lib/format";
import { Badge, Card, Meter } from "@/ui/primitives";

export function TailoredPreview({ cv }: { cv: TailoredCV }) {
  const ats = cv.ats;
  return (
    <Card title="CV adaptado a esta oferta" actions={ats && <Badge tone={atsTone(ats.score)} title="Puntaje de lectura por un ATS">ATS {ats.score}/100</Badge>}>
      {cv.review_required && <p className="error">Revisa el CV antes de usarlo: hay frases que no pudimos respaldar con tu perfil.</p>}
      {ats && (
        <>
          <Meter value={ats.keyword_coverage * 100} tone={atsTone(ats.keyword_coverage * 100)} label="Palabras clave cubiertas" />
          <p className="muted small">{Math.round(ats.keyword_coverage * 100)}% de las palabras clave de la oferta que SÍ tienes están en el CV.</p>
          {ats.issues.map((i) => <p key={i} className="error">{i}</p>)}
          {ats.warnings.map((w) => <p key={w} className="muted">{w}</p>)}
        </>
      )}
      <details>
        <summary>Ver texto del CV</summary>
        <pre className="cvtext">{cv.text}</pre>
      </details>
    </Card>
  );
}
