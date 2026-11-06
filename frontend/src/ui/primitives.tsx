/** Primitivas visuales: sin lógica, sin datos, sin red. Todo entra por props. */
import type { ButtonHTMLAttributes, ReactNode } from "react";
import type { Tone } from "@/lib/format";
import { hueOf } from "@/lib/search";

export function Button({ variant = "primary", busy, icon, children, ...p }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost"; busy?: boolean; icon?: ReactNode }) {
  return (
    <button {...p} disabled={p.disabled || busy} aria-busy={busy || undefined} className={`btn btn-${variant} ${p.className ?? ""}`}>
      {busy ? <span className="dots" aria-hidden /> : icon}{children}
    </button>
  );
}
export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return <span className={`badge badge-${tone}`} title={title}>{children}</span>;
}
export function Card({ title, subtitle, actions, children, className = "" }: { title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          <div><h2>{title}</h2>{subtitle && <p className="muted small">{subtitle}</p>}</div>
          {actions && <div className="row">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}
/** Esqueleto con la forma del contenido final (mejor que un spinner). */
export function Skeleton({ lines = 3, className = "" }: { lines?: number; className?: string }) {
  return <div className={`skeleton-stack ${className}`} role="status" aria-label="Cargando">{Array.from({ length: lines }, (_, i) => <span key={i} className="skeleton" style={{ width: `${92 - i * 14}%` }} />)}</div>;
}
export function Spinner({ label = "Cargando…" }: { label?: string }) {
  return <p role="status" className="muted loading"><span className="dots" aria-hidden />{label}</p>;
}
export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return <p role="alert" className="error">{error instanceof Error ? error.message : "Algo salió mal"}</p>;
}
export function Empty({ icon, title, hint, action }: { icon?: ReactNode; title: string; hint?: string; action?: ReactNode }) {
  return <div className="empty">{icon && <div className="empty-icon">{icon}</div>}<strong>{title}</strong>{hint && <p className="muted">{hint}</p>}{action}</div>;
}
export function Meter({ value, max = 100, tone = "neutral", label }: { value: number; max?: number; tone?: Tone; label: string }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return <div className="meter" role="meter" aria-label={label} aria-valuenow={Math.round(value)} aria-valuemin={0} aria-valuemax={max}><div className={`meter-bar tone-${tone}`} style={{ width: `${pct}%` }} /></div>;
}
/** Iniciales sobre un color estable derivado del nombre. */
export function Avatar({ name, size = 40 }: { name: string; size?: number }) {
  const ini = (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("") || "?";
  const h = hueOf(name || "?");
  return <span className="avatar" aria-hidden style={{ width: size, height: size, fontSize: size * 0.38, background: `hsl(${h} 38% 24%)`, color: `hsl(${h} 70% 82%)`, borderColor: `hsl(${h} 38% 32%)` }}>{ini}</span>;
}
/** Anillo de puntaje 0-100. */
export function ScoreRing({ value, label, tone = "neutral", size = 52 }: { value: number; label: string; tone?: Tone; size?: number }) {
  const r = 20, c = 2 * Math.PI * r, pct = Math.max(0, Math.min(100, value));
  return (
    <span className={`ring tone-text-${tone}`} style={{ width: size, height: size }} role="img" aria-label={`Puntaje ${label}`}>
      <svg viewBox="0 0 48 48" width={size} height={size}>
        <circle cx="24" cy="24" r={r} className="ring-track" /><circle cx="24" cy="24" r={r} className="ring-bar" strokeDasharray={c} strokeDashoffset={c * (1 - pct / 100)} transform="rotate(-90 24 24)" />
      </svg>
      <b>{label}</b>
    </span>
  );
}
