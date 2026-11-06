import type { EmploymentType, WorkMode } from "@/api/types";

export const MODES: { id: WorkMode; label: string }[] = [{ id: "remote", label: "Remoto" }, { id: "hybrid", label: "Híbrido" }, { id: "onsite", label: "Presencial" }];
export const CONTRACTS: { id: EmploymentType | ""; label: string }[] = [{ id: "", label: "Cualquier contrato" }, { id: "full_time", label: "Tiempo completo" }, { id: "part_time", label: "Medio tiempo" }, { id: "contract", label: "Por contrato" }, { id: "internship", label: "Prácticas" }];
export const AGES = [{ h: 0, label: "Cualquier fecha" }, { h: 24, label: "Últimas 24 horas" }, { h: 72, label: "Últimos 3 días" }, { h: 168, label: "Última semana" }, { h: 720, label: "Último mes" }];
export const COUNTRIES: { code: string; name: string }[] = [{ code: "", name: "Cualquier país" }, { code: "CO", name: "Colombia" }, { code: "MX", name: "México" }, { code: "AR", name: "Argentina" }, { code: "CL", name: "Chile" }, { code: "PE", name: "Perú" }, { code: "EC", name: "Ecuador" }, { code: "UY", name: "Uruguay" }, { code: "BR", name: "Brasil" }, { code: "ES", name: "España" }, { code: "US", name: "Estados Unidos" }, { code: "CA", name: "Canadá" }];


export interface OptionsValue { modes: WorkMode[]; country: string; contract: EmploymentType | ""; hours: number }
export const EMPTY_OPTIONS: OptionsValue = { modes: [], country: "", contract: "", hours: 0 };
export const optionsCount = (o: OptionsValue) => o.modes.length + (o.contract ? 1 : 0) + (o.country ? 1 : 0) + (o.hours ? 1 : 0);

/** Modalidad, país, contrato y antigüedad: los mismos filtros al buscar ahora y al programar una búsqueda. */
export function SearchOptionsFields({ value, onChange }: { value: OptionsValue; onChange: (v: OptionsValue) => void }) {
  const toggle = (m: WorkMode) => onChange({ ...value, modes: value.modes.includes(m) ? value.modes.filter((y) => y !== m) : [...value.modes, m] });
  return (
    <>
      <div className="seg" role="group" aria-label="Modalidad">
        {MODES.map((m) => <button key={m.id} type="button" className={`seg-item ${value.modes.includes(m.id) ? "is-active" : ""}`} aria-pressed={value.modes.includes(m.id)} onClick={() => toggle(m.id)}>{m.label}</button>)}
      </div>
      <select aria-label="País" value={value.country} onChange={(e) => onChange({ ...value, country: e.target.value })}>{COUNTRIES.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}</select>
      <select aria-label="Contrato" value={value.contract} onChange={(e) => onChange({ ...value, contract: e.target.value as EmploymentType | "" })}>{CONTRACTS.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}</select>
      <select aria-label="Publicada" value={value.hours} onChange={(e) => onChange({ ...value, hours: Number(e.target.value) })}>{AGES.map((a) => <option key={a.h} value={a.h}>{a.label}</option>)}</select>
    </>
  );
}
