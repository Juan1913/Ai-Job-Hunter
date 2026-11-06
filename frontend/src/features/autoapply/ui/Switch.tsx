import type { ReactNode } from "react";

/** Interruptor accesible con título y explicación. */
export function Switch({ checked, onChange, title, hint, tone = "accent", disabled }: { checked: boolean; onChange: (v: boolean) => void; title: ReactNode; hint?: ReactNode; tone?: "accent" | "warn"; disabled?: boolean }) {
  return (
    <label className={`aa-switch ${checked ? "is-on" : ""} aa-switch-${tone}`}>
      <span className="aa-switch-text"><b>{title}</b>{hint && <span className="muted small">{hint}</span>}</span>
      <input type="checkbox" role="switch" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="aa-track" aria-hidden><span /></span>
    </label>
  );
}
