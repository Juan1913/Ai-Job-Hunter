import { ArrowUpRight, MapPin } from "@phosphor-icons/react";
import type { MapData, MapPoint } from "@/api/types";
import { scoreTone, statusLabel, sourceName } from "@/lib/format";
import { Avatar, Badge, Empty } from "@/ui/primitives";

export interface PlacePanelProps { data: MapData; point: MapPoint | null; onPick: (place: string) => void; onOpenJob: (id: number) => void }

/** Lista junto al mapa: los lugares con más ofertas y, al elegir uno, sus ofertas. */
export function PlacePanel({ data, point, onPick, onOpenJob }: PlacePanelProps) {
  if (point) {
    return (
      <div className="place">
        <div className="place-head"><MapPin size={18} /><div><h2>{point.place}</h2><p className="muted small">{point.count} {point.count === 1 ? "oferta" : "ofertas"}{point.precision === "country" ? " · ubicación solo a nivel país" : ""}</p></div></div>
        <ul className="place-jobs">
          {point.jobs.map((j) => (
            <li key={j.id}>
              <button className="place-job" onClick={() => onOpenJob(j.id)}>
                <Avatar name={j.company || j.source} size={34} />
                <span className="place-job-text"><b>{j.title}</b><span className="muted small block">{[j.company, sourceName(j.source)].filter(Boolean).join(" · ")}</span></span>
                <span className="place-job-side">{j.score != null && <Badge tone={scoreTone(j.score)}>{j.score.toFixed(1)}</Badge>}{j.status !== "new" && <span className="muted small">{statusLabel[j.status as keyof typeof statusLabel] ?? j.status}</span>}</span>
                <ArrowUpRight size={16} className="muted" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      </div>
    );
  }
  if (data.points.length === 0) return <Empty icon={<MapPin size={28} />} title="Sin ubicaciones todavía" hint="Cuando tus búsquedas traigan ofertas con ciudad o país, aparecerán aquí." />;
  return (
    <div className="place">
      <h2>Dónde hay más ofertas</h2>
      <ol className="place-rank">
        {data.points.slice(0, 12).map((p) => (
          <li key={p.place}><button onClick={() => onPick(p.place)}><span className="rank-name">{p.place}</span><span className="rank-bar"><span style={{ width: `${(p.count / data.points[0]!.count) * 100}%` }} /></span><b>{p.count}</b></button></li>
        ))}
      </ol>
    </div>
  );
}
