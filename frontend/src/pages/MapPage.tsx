import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, Globe, House, MapPinLine } from "@phosphor-icons/react";
import { useJobsMap } from "@/features/map/useMap";
import { JobsMap } from "@/features/map/ui/JobsMap";
import { PlacePanel } from "@/features/map/ui/PlacePanel";
import { MetricCard } from "@/features/dashboard/ui/MetricCard";
import { Button, ErrorNote, Skeleton } from "@/ui/primitives";

export function MapPage() {
  const nav = useNavigate();
  const map = useJobsMap();
  const [selected, setSelected] = useState<string | null>(null);
  const point = useMemo(() => map.data?.points.find((p) => p.place === selected) ?? null, [map.data, selected]);
  if (map.isLoading) return <Skeleton lines={6} />;
  if (!map.data) return <ErrorNote error={map.error} />;
  const d = map.data;
  return (
    <div className="stack-lg">
      <header className="page-head"><h1>Mapa de ofertas</h1><p className="muted">Dónde están las vacantes que encontraste. El color indica el mejor encaje de cada lugar.</p></header>
      <section className="metrics metrics-3" aria-label="Resumen del mapa">
        <MetricCard icon={<MapPinLine size={20} />} label="En el mapa" value={d.located} hint={`${d.points.length} lugares`} tone="good" />
        <MetricCard icon={<House size={20} />} label="Remotas" value={d.remote} hint="Sin ubicación fija" />
        <MetricCard icon={<Globe size={20} />} label="Sin ubicar" value={d.unlocated} hint="Lugar no reconocido" tone={d.unlocated ? "warn" : "neutral"} />
      </section>
      <div className="map-layout">
        <div className="map-wrap"><JobsMap points={d.points} selected={selected} onSelect={setSelected} />
          <ul className="legend" aria-label="Leyenda"><li><i style={{ background: "var(--good)" }} />Vale la pena</li><li><i style={{ background: "var(--warn)" }} />Considerar</li><li><i style={{ background: "var(--bad)" }} />Poco encaje</li><li><i className="legend-dash" />Solo país</li></ul>
        </div>
        <aside className="card map-side"><div className="map-side-in">
          {point && <Button variant="ghost" icon={<ArrowLeft size={16} />} onClick={() => setSelected(null)}>Todos los lugares</Button>}
          <PlacePanel data={d} point={point} onPick={setSelected} onOpenJob={(id) => nav(`/jobs/${id}`)} />
        </div></aside>
      </div>
    </div>
  );
}
