import { useState } from "react";
import { MagnifyingGlass, SlidersHorizontal, X } from "@phosphor-icons/react";
import type { EmploymentType, JobFilters, Recommendation, WorkMode } from "@/api/types";
import { recommendationLabel } from "@/lib/format";
import { activeFilterCount } from "@/lib/filters";
import { COUNTRIES } from "./SearchOptionsFields";

const REC: { value: Recommendation | undefined; label: string }[] = [
  { value: undefined, label: "Todas" },
  ...(Object.keys(recommendationLabel) as Recommendation[]).map((r) => ({ value: r, label: recommendationLabel[r] })),
];
const MODES: { value: WorkMode | undefined; label: string }[] = [{ value: undefined, label: "Cualquiera" }, { value: "remote", label: "Remoto" }, { value: "hybrid", label: "Híbrido" }, { value: "onsite", label: "Presencial" }];
const SORTS: { value: NonNullable<JobFilters["sort"]>; label: string }[] = [{ value: "score", label: "Mejor encaje" }, { value: "recent", label: "Más recientes" }, { value: "salary", label: "Mayor salario" }];
const CONTRACTS: { value: EmploymentType | ""; label: string }[] = [{ value: "", label: "Cualquier contrato" }, { value: "full_time", label: "Tiempo completo" }, { value: "part_time", label: "Medio tiempo" }, { value: "contract", label: "Por contrato" }, { value: "internship", label: "Prácticas" }, { value: "temporary", label: "Temporal" }];
const AGES = [{ d: 0, label: "Cualquier fecha" }, { d: 1, label: "Últimas 24 horas" }, { d: 3, label: "Últimos 3 días" }, { d: 7, label: "Última semana" }, { d: 30, label: "Último mes" }];

/** Filtros de las ofertas ya guardadas: rol o especialidad, modalidad, salario, contrato, antigüedad, país y orden. */
export function JobFiltersBar({ value, onChange }: { value: JobFilters; onChange: (f: JobFilters) => void }) {
  const [more, setMore] = useState(false);
  const set = (patch: Partial<JobFilters>) => onChange({ ...value, ...patch, offset: 0 });
  const n = activeFilterCount(value);
  return (
    <div className="filters-wrap">
      <div className="filters" role="search">
        <label className="search-input"><MagnifyingGlass size={18} aria-hidden />
          <input type="search" placeholder="Rol, especialidad, tecnología o empresa" aria-label="Buscar" value={value.q ?? ""} onChange={(e) => set({ q: e.target.value || undefined })} />
        </label>
        <label className="filter-sort">Ordenar<select aria-label="Ordenar por" value={value.sort ?? "score"} onChange={(e) => set({ sort: e.target.value as JobFilters["sort"] })}>{SORTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}</select></label>
        <button type="button" className={`btn btn-secondary ${more ? "is-open" : ""}`} aria-expanded={more} onClick={() => setMore((v) => !v)}><SlidersHorizontal size={16} />Más filtros{n > 0 ? ` (${n})` : ""}</button>
        {n > 0 && <button type="button" className="btn btn-ghost" onClick={() => onChange({ sort: value.sort })}><X size={14} />Limpiar</button>}
      </div>
      <div className="filter-row">
        <div className="seg" role="group" aria-label="Modalidad">
          {MODES.map((m) => <button key={m.label} type="button" className={`seg-item ${value.workMode === m.value ? "is-active" : ""}`} aria-pressed={value.workMode === m.value} onClick={() => set({ workMode: m.value })}>{m.label}</button>)}
        </div>
        <div className="seg" role="group" aria-label="Recomendación">
          {REC.map((c) => <button key={c.label} type="button" className={`seg-item ${value.recommendation === c.value ? "is-active" : ""}`} aria-pressed={value.recommendation === c.value} onClick={() => set({ recommendation: c.value })}>{c.label}</button>)}
        </div>
      </div>
      {more && (
        <div className="filter-more">
          <label className="aa-field">Salario anual mínimo<input type="number" min={0} step={1000000} inputMode="numeric" placeholder="Ej.: 60000000" value={value.minSalary ?? ""} onChange={(e) => set({ minSalary: e.target.value ? Number(e.target.value) : undefined })} /><span className="muted small">Solo ofertas que publican salario.</span></label>
          <label className="aa-field">Contrato<select value={value.employmentType ?? ""} onChange={(e) => set({ employmentType: (e.target.value || undefined) as EmploymentType | undefined })}>{CONTRACTS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}</select></label>
          <label className="aa-field">Publicada<select value={value.postedDays ?? 0} onChange={(e) => set({ postedDays: Number(e.target.value) || undefined })}>{AGES.map((a) => <option key={a.d} value={a.d}>{a.label}</option>)}</select></label>
          <label className="aa-field">País<select value={value.country ?? ""} onChange={(e) => set({ country: e.target.value || undefined })}>{COUNTRIES.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}</select></label>
        </div>
      )}
    </div>
  );
}
