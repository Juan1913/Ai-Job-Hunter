import type { ReactNode } from "react";
import { CountUp } from "@/ui/CountUp";
import { Sparkline } from "@/ui/Sparkline";

export interface MetricCardProps { icon: ReactNode; label: string; value: ReactNode | number; decimals?: number; suffix?: string; hint?: string; tone?: "good" | "warn" | "bad" | "neutral"; spark?: readonly number[] }
export function MetricCard({ icon, label, value, decimals = 0, suffix, hint, tone = "neutral", spark }: MetricCardProps) {
  return (
    <div className="metric">
      <div className="metric-top"><p className="metric-label">{label}</p><span className={`metric-icon tone-chip-${tone}`} aria-hidden>{icon}</span></div>
      <p className="metric-value">{typeof value === "number" ? <CountUp value={value} decimals={decimals} /> : value}{suffix && <small className="muted"> {suffix}</small>}</p>
      <div className="metric-foot"><p className="muted small">{hint ?? " "}</p>{spark && <Sparkline data={spark} label={`Tendencia: ${label}`} />}</div>
    </div>
  );
}
