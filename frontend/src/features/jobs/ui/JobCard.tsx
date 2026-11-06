import { BookmarkSimple, MapPin, Money } from "@phosphor-icons/react";
import type { JobOut, Recommendation } from "@/api/types";
import { formatSalary, text, recommendationLabel, recommendationTone, relativeDays, scoreTone, sourceName, statusLabel } from "@/lib/format";
import { Avatar, Badge, ScoreRing } from "@/ui/primitives";

export interface JobCardProps { item: JobOut; onOpen: (id: number) => void; onToggleSave?: (id: number, saved: boolean) => void }

/** Tarjeta de oferta: empresa, cargo, datos clave y el puntaje de encaje. */
export function JobCard({ item, onOpen, onToggleSave }: JobCardProps) {
  const { job, evaluation } = item;
  const rec = evaluation?.recommendation as Recommendation | undefined;
  const salary = formatSalary(job);
  const posted = relativeDays(job.posted_at);
  const saved = item.status === "saved";
  const pct = evaluation ? (evaluation.score / 5) * 100 : item.match ?? 0;
  const label = evaluation ? evaluation.score.toFixed(1) : item.match != null ? `${Math.round(item.match)}` : "-";
  return (
    <article className="job" onClick={() => onOpen(item.id)} tabIndex={0} onKeyDown={(e) => { if (e.key === "Enter") onOpen(item.id); }} aria-label={`${text(job.title)}, ${text(job.company)}`}>
      <header className="job-top">
        <Avatar name={job.company || job.source} />
        <div className="job-who"><p className="job-company">{text(job.company) || sourceName(job.source)}</p><p className="muted small">{[sourceName(job.source), posted].filter(Boolean).join(" · ")}</p></div>
        {onToggleSave && <button className={`icon-btn ${saved ? "is-on" : ""}`} aria-label={saved ? "Quitar de guardadas" : "Guardar oferta"} aria-pressed={saved} onClick={(e) => { e.stopPropagation(); onToggleSave(item.id, !saved); }}><BookmarkSimple size={20} weight={saved ? "fill" : "regular"} /></button>}
      </header>
      <h3 className="job-title">{text(job.title)}</h3>
      <ul className="job-meta">
        {(job.location || job.remote) && <li><MapPin size={15} />{job.location || "Remoto"}{job.remote && job.location ? " · Remoto" : ""}</li>}
        {salary && <li><Money size={15} />{salary}</li>}
      </ul>
      <footer className="job-foot">
        <div className="job-tags">
          {rec && <Badge tone={recommendationTone[rec]}>{recommendationLabel[rec]}</Badge>}
          {item.status !== "new" && <Badge>{statusLabel[item.status as keyof typeof statusLabel] ?? item.status}</Badge>}
          {job.closed && <Badge tone="bad">Cerrada</Badge>}
        </div>
        {evaluation || item.match != null ? <ScoreRing value={pct} label={label} tone={evaluation ? scoreTone(evaluation.score) : "neutral"} /> : <span className="job-unscored">Sin evaluar</span>}
      </footer>
    </article>
  );
}
