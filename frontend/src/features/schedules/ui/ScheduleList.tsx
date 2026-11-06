import { useState } from "react";
import { ArrowsClockwise, Pause, Play, Trash } from "@phosphor-icons/react";
import type { SavedSearch } from "@/api/types";
import { intervalLabel, paramChips, pausedWhy, whenLabel } from "@/lib/schedules";
import { Badge, Button, Card, Empty } from "@/ui/primitives";

export interface ScheduleListProps {
  items: readonly SavedSearch[];
  busy: boolean;
  onRun: (id: number) => void;
  onPause: (id: number) => void;
  onResume: (id: number) => void;
  onDelete: (id: number) => void;
}

function Item({ s, busy, onRun, onPause, onResume, onDelete }: { s: SavedSearch } & Omit<ScheduleListProps, "items">) {
  const [sure, setSure] = useState(false);
  const chips = paramChips(s.params);
  const roles = s.params.keywords?.length ? s.params.keywords.join(", ") : "los roles de tu perfil";
  return (
    <li className={`sch-item ${s.active ? "" : "is-paused"}`}>
      <div className="sch-main">
        <div className="row"><b>{s.name}</b><Badge tone={s.active ? "good" : "warn"}>{s.active ? "Activa" : "En pausa"}</Badge></div>
        <p className="muted small">Busca {roles} · {intervalLabel(s.interval_minutes)}{s.window_start && s.window_end ? ` · de ${s.window_start} a ${s.window_end}` : ""}</p>
        {chips.length > 0 && <div className="chips">{chips.map((c) => <Badge key={c}>{c}</Badge>)}</div>}
        <p className="muted small">{s.active ? `Próxima: ${whenLabel(s.next_run_at)}` : pausedWhy(s)} · Última: {s.last_run_at ? whenLabel(s.last_run_at) : "aún no corrió"} · {s.total_runs} {s.total_runs === 1 ? "ejecución" : "ejecuciones"}</p>
        {s.last_error && s.active && <p className="error small">Último error: {s.last_error}</p>}
      </div>
      <div className="sch-actions">
        <Button variant="secondary" busy={busy} icon={<ArrowsClockwise size={16} />} onClick={() => onRun(s.id)}>Ejecutar ahora</Button>
        {s.active ? <Button variant="ghost" busy={busy} icon={<Pause size={16} />} onClick={() => onPause(s.id)}>Pausar</Button> : <Button variant="ghost" busy={busy} icon={<Play size={16} />} onClick={() => onResume(s.id)}>Reanudar</Button>}
        {sure ? <Button variant="danger" busy={busy} onClick={() => onDelete(s.id)}>Sí, eliminar</Button> : <button className="icon-btn" aria-label={`Eliminar ${s.name}`} onClick={() => setSure(true)}><Trash size={18} /></button>}
      </div>
    </li>
  );
}
export function ScheduleList({ items, ...h }: ScheduleListProps) {
  return (
    <Card title="Tus búsquedas programadas" subtitle="Trabajan solas mientras el trabajador esté activo">
      {items.length === 0 ? <Empty title="Aún no programaste ninguna" hint="Crea una y el sistema buscará solo, sin que abras la página." /> : <ul className="sch-list">{items.map((s) => <Item key={s.id} s={s} {...h} />)}</ul>}
    </Card>
  );
}
