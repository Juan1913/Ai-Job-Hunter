import { ArrowSquareOut, Trash } from "@phosphor-icons/react";
import type { Portal } from "@/api/types";
import { canAutomate, portalKindLabel } from "@/lib/autoapply";
import { Avatar, Badge, Card, Empty } from "@/ui/primitives";

export interface PortalListProps { portals: readonly Portal[]; automatable: readonly string[]; onDelete: (id: number) => void }

/** Portales y páginas de empleo que se recorren en cada búsqueda: los del sistema y los que añadió el usuario. */
export function PortalList({ portals, automatable, onDelete }: PortalListProps) {
  const mine = portals.filter((p) => !p.global);
  const shared = portals.filter((p) => p.global);
  const row = (p: Portal) => (
    <li key={p.id} className="pt-item">
      <Avatar name={p.name} size={36} />
      <div className="pt-main"><b>{p.name}</b>{p.url && <a className="muted small pt-url" href={p.url} target="_blank" rel="noopener noreferrer">{p.url.replace(/^https?:\/\//, "")}<ArrowSquareOut size={12} /></a>}</div>
      <Badge>{portalKindLabel(p.kind)}</Badge>
      <Badge tone={canAutomate(p.kind, automatable) ? "good" : "neutral"} title={canAutomate(p.kind, automatable) ? "Puede postular el sistema" : "Solo postulas tú"}>{canAutomate(p.kind, automatable) ? "Auto-postulable" : "Solo manual"}</Badge>
      {!p.global && <button className="icon-btn" aria-label={`Quitar ${p.name}`} onClick={() => onDelete(p.id)}><Trash size={18} /></button>}
    </li>
  );
  return (
    <Card title="Portales que se recorren" subtitle="Las ofertas de estas páginas aparecen en tus búsquedas">
      {mine.length === 0 && <Empty title="Aún no añadiste ninguno" hint="Pega la página de empleo de una empresa o portal y la incluimos en cada búsqueda." />}
      {mine.length > 0 && <><h3>Los tuyos</h3><ul className="pt-list">{mine.map(row)}</ul></>}
      {shared.length > 0 && <><h3>Incluidos en el sistema ({shared.length})</h3><ul className="pt-list pt-list-scroll">{shared.map(row)}</ul></>}
    </Card>
  );
}
