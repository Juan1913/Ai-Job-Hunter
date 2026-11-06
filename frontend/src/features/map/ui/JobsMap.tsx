import { useEffect, useMemo, useRef, useState } from "react";
import { geoGraticule10, geoNaturalEarth1, geoPath } from "d3-geo";
import { select } from "d3-selection";
import "d3-transition";
import { zoom, zoomIdentity, type ZoomBehavior } from "d3-zoom";
import { feature } from "topojson-client";
import type { GeometryCollection, Topology } from "topojson-specification";
import { Minus, Plus, ArrowsIn } from "@phosphor-icons/react";
import world from "world-atlas/countries-110m.json";
import type { MapPoint } from "@/api/types";

const W = 960, H = 500;
const toneOf = (s: number | null) => (s == null ? "neutral" : s >= 3.8 ? "good" : s >= 3 ? "warn" : "bad");
export interface JobsMapProps { points: MapPoint[]; selected: string | null; dark?: boolean; onSelect: (place: string) => void }

/** Mapamundi SVG propio (sin tiles ni red): siluetas, retícula y una burbuja por lugar. Presentacional: recibe puntos y avisa clics. */
export function JobsMap({ points, selected, onSelect }: JobsMapProps) {
  const svg = useRef<SVGSVGElement>(null);
  const [t, setT] = useState({ k: 1, x: 0, y: 0 });
  const [hover, setHover] = useState<MapPoint | null>(null);
  const zb = useRef<ZoomBehavior<SVGSVGElement, unknown> | null>(null);
  const projection = useMemo(() => geoNaturalEarth1().fitExtent([[8, 8], [W - 8, H - 8]], { type: "Sphere" }), []);
  const path = useMemo(() => geoPath(projection), [projection]);
  const land = useMemo(() => {
    const topo = world as unknown as Topology<{ countries: GeometryCollection }>;
    return (feature(topo, topo.objects.countries) as unknown as { features: { id?: string | number }[] }).features.map((f) => ({ id: String(f.id), d: path(f as never) ?? "" }));
  }, [path]);
  const grid = useMemo(() => path(geoGraticule10()) ?? "", [path]);
  const sphere = useMemo(() => path({ type: "Sphere" }) ?? "", [path]);
  const max = Math.max(1, ...points.map((p) => p.count));
  const placed = useMemo(() => points.map((p) => ({ p, xy: projection([p.lon, p.lat]) })).filter((q): q is { p: MapPoint; xy: [number, number] } => !!q.xy), [points, projection]);

  useEffect(() => {
    if (!svg.current) return;
    const z = zoom<SVGSVGElement, unknown>().scaleExtent([1, 8]).translateExtent([[0, 0], [W, H]]).extent([[0, 0], [W, H]]).on("zoom", (e) => setT({ k: e.transform.k, x: e.transform.x, y: e.transform.y }));
    zb.current = z;
    const node = svg.current;
    select(node).call(z);
    return () => { select(node).on(".zoom", null); };
  }, []);
  const by = (k: number) => svg.current && zb.current && select(svg.current).transition().duration(250).call(zb.current.scaleBy, k);
  const reset = () => svg.current && zb.current && select(svg.current).transition().duration(300).call(zb.current.transform, zoomIdentity);

  return (
    <div className="worldmap">
      <svg ref={svg} viewBox={`0 0 ${W} ${H}`} role="application" aria-label="Mapamundi de ofertas por ubicación" preserveAspectRatio="xMidYMid meet">
        <g transform={`translate(${t.x} ${t.y}) scale(${t.k})`}>
          <path d={sphere} className="wm-sphere" />
          <path d={grid} className="wm-grid" />
          {land.map((c, i) => <path key={`${c.id}-${i}`} d={c.d} className="wm-land" />)}
          {placed.map(({ p, xy }, i) => {
            const r = (5 + Math.sqrt(p.count / max) * 16) / Math.sqrt(t.k);
            const on = p.place === selected;
            return (
              <g key={p.place} transform={`translate(${xy[0]} ${xy[1]})`} className={`wm-dot tone-${toneOf(p.best_score)} ${on ? "is-on" : ""} ${p.precision === "country" ? "is-country" : ""}`} style={{ animationDelay: `${Math.min(i, 20) * 40}ms` }}
                tabIndex={0} role="button" aria-label={`${p.place}: ${p.count} ofertas`} onClick={() => onSelect(p.place)} onKeyDown={(e) => { if (e.key === "Enter") onSelect(p.place); }}
                onMouseEnter={() => setHover(p)} onMouseLeave={() => setHover(null)} onFocus={() => setHover(p)} onBlur={() => setHover(null)}>
                {p.best_score != null && p.best_score >= 3.8 && <circle r={r} className="wm-pulse" />}
                <circle r={r} className="wm-bubble" />
                {p.count > 1 && r > 9 && <text className="wm-count" textAnchor="middle" dy=".35em" fontSize={11 / Math.sqrt(t.k)}>{p.count}</text>}
              </g>
            );
          })}
        </g>
      </svg>
      {hover && <div className="wm-tip" role="status">{hover.place}<span className="muted small"> · {hover.count} {hover.count === 1 ? "oferta" : "ofertas"}{hover.precision === "country" ? " (solo país)" : ""}</span></div>}
      <div className="wm-zoom" role="group" aria-label="Zoom">
        <button onClick={() => by(1.6)} aria-label="Acercar"><Plus size={16} /></button>
        <button onClick={() => by(1 / 1.6)} aria-label="Alejar"><Minus size={16} /></button>
        <button onClick={reset} aria-label="Restablecer vista"><ArrowsIn size={16} /></button>
      </div>
    </div>
  );
}
