import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Briefcase, ChartBar, Sparkle, Star, UploadSimple } from "@phosphor-icons/react";
import { useAllJobs, useElapsed, useSearch } from "@/features/jobs/useJobs";
import { useProfile } from "@/features/profile/useProfile";
import { MetricCard } from "@/features/dashboard/ui/MetricCard";
import { BarList, Funnel, Histogram } from "@/features/dashboard/ui/Charts";
import { SearchBar, SearchLive } from "@/features/jobs/ui/SearchPanel";
import { JobCard } from "@/features/jobs/ui/JobCard";
import { computeMetrics, perDay, topMatches } from "@/lib/metrics";
import { Button, Card, Empty, Skeleton } from "@/ui/primitives";
import { CountUp } from "@/ui/CountUp";
import { useSaveToggle } from "./useSaveToggle";

export function DashboardPage() {
  const nav = useNavigate();
  const profile = useProfile();
  const jobs = useAllJobs();
  const search = useSearch();
  const elapsed = useElapsed(search.startedAt, search.running);
  const save = useSaveToggle();
  const items = jobs.data?.items;
  const m = useMemo(() => computeMetrics(items ?? []), [items]);
  const top = useMemo(() => topMatches(items ?? []), [items]);
  const trend = useMemo(() => perDay(items ?? []), [items]);
  const name = profile.data?.full_name?.split(" ")[0];

  if (profile.isSuccess && !profile.data) {
    return (
      <div className="stack-lg">
        <header className="page-head"><h1>Empecemos</h1></header>
        <Empty icon={<UploadSimple size={28} />} title="Sube tu CV para activar el panel" hint="Lo leemos, armamos tu perfil y buscamos ofertas que encajen contigo. Tú revisas todo antes de usarlo." action={<Button onClick={() => nav("/profile")}>Subir mi CV</Button>} />
      </div>
    );
  }
  const ringPct = m.avgScore != null ? (m.avgScore / 5) * 100 : 0;
  return (
    <div className="stack-lg">
      <section className="hero" aria-label="Resumen">
        <div className="hero-copy">
          <span className="hero-eyebrow"><Sparkle size={14} weight="fill" /> Tu búsqueda de hoy</span>
          <div>
            <h1>{name ? `Hola, ${name}` : "Panel"}</h1>
            <p className="muted" style={{ marginTop: 10, fontSize: "1.05rem" }}>{m.total > 0 ? `${m.fresh} ofertas nuevas esta semana, ${m.worthApplying} valen la pena.` : "Aún no hay ofertas. Lanza tu primera búsqueda."}</p>
          </div>
          <SearchBar running={search.running} starting={search.starting} error={search.error} onStart={search.start} />
        </div>
        <div className="hero-stat">
          <div className="hero-ring" role="img" aria-label={m.avgScore != null ? `Encaje medio ${m.avgScore.toFixed(1)} de 5` : "Sin evaluar"}>
            <svg viewBox="0 0 48 48"><circle cx="24" cy="24" r="21" className="ring-track" fill="none" /><circle cx="24" cy="24" r="21" className="ring-bar" fill="none" strokeDasharray={2 * Math.PI * 21} strokeDashoffset={2 * Math.PI * 21 * (1 - ringPct / 100)} transform="rotate(-90 24 24)" /></svg>
            <div>{m.avgScore != null ? <b><CountUp value={m.avgScore} decimals={1} /></b> : <b>-</b>}<small>de 5</small></div>
          </div>
          <p className="muted small">Encaje medio con tu perfil</p>
        </div>
      </section>

      <SearchLive view={search.view} running={search.running} elapsed={elapsed} onDismiss={search.dismiss} onSeeJobs={() => nav("/jobs")} />

      <section className="metrics" aria-label="Métricas">
        <MetricCard icon={<Briefcase size={20} />} label="Ofertas guardadas" value={m.total} hint={`${m.fresh} esta semana`} spark={trend} />
        <MetricCard icon={<Sparkle size={20} />} label="Valen la pena" value={m.worthApplying} tone="good" hint={m.evaluated ? `de ${m.evaluated} evaluadas` : "aún sin evaluar"} />
        <MetricCard icon={<Star size={20} />} label="Puntaje medio" value={m.avgScore ?? "-"} decimals={1} suffix={m.avgScore != null ? "/ 5" : undefined} tone={m.avgScore != null && m.avgScore >= 3.8 ? "good" : "warn"} />
        <MetricCard icon={<ChartBar size={20} />} label="Postuladas" value={m.pipeline.applied} hint={`${m.pipeline.interview} en entrevista`} />
      </section>

      {jobs.isLoading ? <Skeleton lines={4} /> : m.total > 0 && (
        <div className="grid-3">
          <Card title="Distribución de puntajes" subtitle="Cuántas ofertas caen en cada rango (1 a 5)"><Histogram data={m.scoreBuckets} label="Ofertas por rango de puntaje" /></Card>
          <Card title="De dónde vienen"><BarList data={m.topSources} empty="Sin datos" /></Card>
          <Card title="Tu embudo" subtitle="Lo que tú decides"><Funnel steps={[{ label: "Guardadas", count: m.pipeline.saved }, { label: "Postuladas", count: m.pipeline.applied }, { label: "Entrevistas", count: m.pipeline.interview }, { label: "Ofertas", count: m.pipeline.offer }]} /></Card>
        </div>
      )}

      {top.length > 0 && (
        <section aria-label="Mejores coincidencias">
          <div className="section-head"><h2>Mejores coincidencias</h2><Button variant="secondary" onClick={() => nav("/jobs")}>Ver todas</Button></div>
          <div className="jobs-grid">{top.map((it) => <JobCard key={it.id} item={it} onOpen={(id) => nav(`/jobs/${id}`)} onToggleSave={save} />)}</div>
        </section>
      )}
    </div>
  );
}
