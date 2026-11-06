import { useState, type FormEvent } from "react";
import { ArrowRight, CheckCircle, Circle, MagnifyingGlass, Prohibit, SlidersHorizontal, WarningCircle, X } from "@phosphor-icons/react";
import type { SearchOptions } from "@/api/types";
import { EMPTY_OPTIONS, optionsCount, SearchOptionsFields, type OptionsValue } from "./SearchOptionsFields";
import type { SearchView, SourceRow } from "@/lib/search";
import { clock } from "@/lib/search";
import { Avatar, Button, ErrorNote } from "@/ui/primitives";

export interface SearchPanelProps {
  view: SearchView | null;
  running: boolean;
  starting: boolean;
  elapsed: number;
  error: unknown;
  onStart: (o: SearchOptions) => void;
  onDismiss: () => void;
  onSeeJobs: () => void;
}

const STATE_ICON: Record<SourceRow["state"], { icon: JSX.Element; text: string }> = {
  pending: { icon: <Circle size={18} className="spin-soft" />, text: "Buscando…" },
  done: { icon: <CheckCircle size={18} weight="fill" />, text: "" },
  empty: { icon: <CheckCircle size={18} />, text: "Sin resultados" },
  blocked: { icon: <Prohibit size={18} />, text: "Bloqueada" },
  error: { icon: <WarningCircle size={18} />, text: "Falló" },
};

/** Barra para lanzar la búsqueda: un rol propio (no hace falta que sea el de tu CV) y filtros para esa búsqueda. */
export function SearchBar({ running, starting, error, onStart }: Pick<SearchPanelProps, "running" | "starting" | "error" | "onStart">) {
  const [kw, setKw] = useState("");
  const [open, setOpen] = useState(false);
  const [opts, setOpts] = useState<OptionsValue>(EMPTY_OPTIONS);
  const active = optionsCount(opts);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const list = kw.split(",").map((x) => x.trim()).filter(Boolean);
    onStart({ keywords: list.length ? list : undefined, workModes: opts.modes, countries: opts.country ? [opts.country] : undefined, employmentTypes: opts.contract ? [opts.contract] : undefined, hoursOld: opts.hours || undefined });
  };
  return (
    <>
      <form className="search-bar" onSubmit={submit} role="search">
        <label className="search-input"><MagnifyingGlass size={18} aria-hidden />
          <input value={kw} onChange={(e) => setKw(e.target.value)} placeholder="Rol a buscar, p. ej. «chef, mesero» (vacío = tu perfil)" aria-label="Roles a buscar" disabled={running} />
        </label>
        <Button type="button" variant="secondary" icon={<SlidersHorizontal size={16} />} aria-expanded={open} onClick={() => setOpen((v) => !v)}>Opciones{active > 0 ? ` (${active})` : ""}</Button>
        <Button type="submit" busy={starting || running} disabled={running}>{running ? "Buscando" : "Buscar ahora"}</Button>
      </form>
      {open && <div className="search-opts"><SearchOptionsFields value={opts} onChange={setOpts} /></div>}
      <ErrorNote error={error} />
    </>
  );
}

/** Vista previa en vivo: qué fuentes se recorren, cuánto falta y qué va apareciendo. */
export function SearchLive({ view, running, elapsed, onDismiss, onSeeJobs }: Pick<SearchPanelProps, "view" | "running" | "elapsed" | "onDismiss" | "onSeeJobs">) {
  if (!view) return null;
  return (
    <section className="live-card" aria-label="Búsqueda de ofertas">
      <div className={`live live-${view.phase}`} role="status" aria-live="polite">
          <div className="live-head">
            <div>
              <p className="live-title">{view.label}</p>
              <p className="muted small">
                {view.keywords.length > 0 && <>Buscando: {view.keywords.slice(0, 4).join(", ")}{view.keywords.length > 4 ? ` +${view.keywords.length - 4}` : ""} · </>}
                {view.totalSources > 0 ? `${view.doneSources}/${view.totalSources} fuentes` : "preparando fuentes"} · {view.found} encontradas · {clock(elapsed)}
              </p>
            </div>
            {!running && <button className="icon-btn" onClick={onDismiss} aria-label="Cerrar resultados de la búsqueda"><X size={18} /></button>}
          </div>
          <div className={`progress ${running ? "is-running" : ""}`} aria-hidden><span style={{ width: `${view.progress}%` }} /></div>

          {view.rows.length > 0 && (
            <div className="live-grid">
              <ul className="sources" aria-label="Fuentes">
                {view.rows.map((r) => (
                  <li key={r.key} className={`source source-${r.state}`}>
                    <span className="source-icon">{STATE_ICON[r.state].icon}</span>
                    <span className="source-name">{r.name}</span>
                    <span className="muted small">{r.state === "done" ? `${r.found} ${r.found === 1 ? "oferta" : "ofertas"}${r.seconds != null ? ` · ${r.seconds}s` : ""}` : STATE_ICON[r.state].text}</span>
                  </li>
                ))}
              </ul>
              <div className="feed" aria-label="Vista previa de lo encontrado">
                <p className="muted small feed-title">Lo que va apareciendo</p>
                {view.feed.length === 0 ? (
                  <ul className="feed-list">{[0, 1, 2].map((i) => <li key={i} className="feed-item"><span className="skeleton skeleton-avatar" /><span className="skeleton-stack"><span className="skeleton" style={{ width: "70%" }} /><span className="skeleton" style={{ width: "45%" }} /></span></li>)}</ul>
                ) : (
                  <ul className="feed-list">
                    {view.feed.map((f, i) => (
                      <li key={`${f.source}-${i}`} className="feed-item" style={{ animationDelay: `${Math.min(i, 8) * 40}ms` }}>
                        <Avatar name={f.company || f.source} size={34} />
                        <span><b>{f.title}</b><span className="muted small block">{[f.company, f.location || (f.remote ? "Remoto" : ""), f.source].filter(Boolean).join(" · ")}</span></span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )}

          {view.phase === "done" && (
            <div className="live-foot">
              <p><b>{view.newJobs}</b> {view.newJobs === 1 ? "oferta nueva" : "ofertas nuevas"} guardadas. Tú decides a cuáles postular.</p>
              <Button variant="secondary" onClick={onSeeJobs} icon={<ArrowRight size={16} />}>Ver ofertas</Button>
            </div>
          )}
          {view.phase === "failed" && <p className="error">{view.error ?? "No se pudo completar la búsqueda."}</p>}
      </div>
    </section>
  );
}

/** Lanzador + vista previa juntos (página de ofertas). */
export function SearchPanel(p: SearchPanelProps) {
  return (
    <section className="search" aria-label="Búsqueda de ofertas">
      <SearchBar running={p.running} starting={p.starting} error={p.error} onStart={p.onStart} />
      <SearchLive view={p.view} running={p.running} elapsed={p.elapsed} onDismiss={p.onDismiss} onSeeJobs={p.onSeeJobs} />
    </section>
  );
}
