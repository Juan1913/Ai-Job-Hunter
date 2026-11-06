import { useState, type FormEvent } from "react";
import { CalendarPlus } from "@phosphor-icons/react";
import type { SavedSearchIn, ScheduleLimits } from "@/api/types";
import { INTERVALS } from "@/lib/schedules";
import { Button, Card, ErrorNote } from "@/ui/primitives";
import { EMPTY_OPTIONS, SearchOptionsFields, type OptionsValue } from "@/features/jobs/ui/SearchOptionsFields";

export interface ScheduleFormProps { limits: ScheduleLimits | undefined; busy: boolean; error: unknown; onCreate: (b: SavedSearchIn) => void }

/** Crear una búsqueda que se repite sola. Mismos filtros que «Buscar ahora», más cada cuánto y en qué horario. */
export function ScheduleForm({ limits, busy, error, onCreate }: ScheduleFormProps) {
  const [name, setName] = useState("");
  const [roles, setRoles] = useState("");
  const [opts, setOpts] = useState<OptionsValue>(EMPTY_OPTIONS);
  const min = limits?.min_interval_minutes ?? 60;
  const intervals = INTERVALS.filter((i) => i.minutes >= min);
  const [interval, setInterval] = useState(Math.max(360, min));
  const [timed, setTimed] = useState(false);
  const [from, setFrom] = useState("08:00");
  const [to, setTo] = useState("20:00");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const keywords = roles.split(",").map((x) => x.trim()).filter(Boolean);
    onCreate({
      name: name.trim(), interval_minutes: interval, keywords: keywords.length ? keywords : undefined, work_modes: opts.modes.length ? opts.modes : undefined,
      countries: opts.country ? [opts.country] : undefined, employment_types: opts.contract ? [opts.contract] : undefined, hours_old: opts.hours || undefined,
      ...(timed ? { window_start: from, window_end: to } : {}),
    });
    setName(""); setRoles(""); setOpts(EMPTY_OPTIONS);
  };
  return (
    <Card title="Programar una búsqueda" subtitle="Se repite sola y, si activas la auto-postulación, también postula a lo nuevo que cumpla tus reglas.">
      <form className="sch-form" onSubmit={submit}>
        <label className="aa-field">Nombre<input required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} placeholder="Ej.: Backend remoto" /></label>
        <label className="aa-field">Roles a buscar<input value={roles} onChange={(e) => setRoles(e.target.value)} placeholder="Separados por coma. Vacío = los roles de tu perfil" /></label>
        <div className="search-opts sch-opts"><SearchOptionsFields value={opts} onChange={setOpts} /></div>
        <div className="sch-when">
          <label className="aa-field">Frecuencia<select value={interval} onChange={(e) => setInterval(Number(e.target.value))}>{intervals.map((i) => <option key={i.minutes} value={i.minutes}>{i.label}</option>)}</select></label>
          <label className="check"><input type="checkbox" checked={timed} onChange={(e) => setTimed(e.target.checked)} />Solo en este horario</label>
          {timed && <div className="row"><input type="time" aria-label="Desde" value={from} onChange={(e) => setFrom(e.target.value)} /><span className="muted">a</span><input type="time" aria-label="Hasta" value={to} onChange={(e) => setTo(e.target.value)} /></div>}
        </div>
        <p className="muted small">Frecuencia mínima {min} min: es una cortesía con los portales, para que no bloqueen tu búsqueda.</p>
        <ErrorNote error={error} />
        <div><Button type="submit" busy={busy} icon={<CalendarPlus size={16} />}>Programar</Button></div>
      </form>
    </Card>
  );
}
