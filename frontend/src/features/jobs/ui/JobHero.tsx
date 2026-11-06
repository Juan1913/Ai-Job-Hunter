import type { ReactNode } from "react";
import { MapPin, Money, Globe } from "@phosphor-icons/react";
import type { Job } from "@/api/types";
import { formatSalary, relativeDays, sourceName, text } from "@/lib/format";
import { Avatar, Badge } from "@/ui/primitives";

export interface JobHeroProps { job: Job; appliedLabel: string; applied: boolean; actions: ReactNode; footer?: ReactNode }
/** Cabecera de una oferta: quién, qué, dónde y qué puedes hacer. */
export function JobHero({ job, appliedLabel, applied, actions, footer }: JobHeroProps) {
  const salary = formatSalary(job);
  const posted = relativeDays(job.posted_at);
  return (
    <section className="job-hero">
      <div className="job-hero-top">
        <Avatar name={job.company || job.source} size={60} />
        <div><p className="muted">{text(job.company) || sourceName(job.source)}</p><h1>{text(job.title)}</h1></div>
      </div>
      <div className="job-hero-meta">
        {(job.location || job.remote) && <span className="pill"><MapPin size={14} />{job.location || "Remoto"}{job.remote && job.location ? " · Remoto" : ""}</span>}
        {salary && <span className="pill"><Money size={14} />{salary}</span>}
        <span className="pill"><Globe size={14} />{sourceName(job.source)}{posted ? ` · ${posted}` : ""}</span>
        {job.closed && <Badge tone="bad">Cerrada</Badge>}
        <span className={`applied applied-${applied ? "yes" : "no"}`}>{appliedLabel}</span>
      </div>
      <div className="row">{actions}</div>
      {footer}
    </section>
  );
}
