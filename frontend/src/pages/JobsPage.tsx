import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Binoculars } from "@phosphor-icons/react";
import type { JobFilters } from "@/api/types";
import { useElapsed, useJobs, useSearch } from "@/features/jobs/useJobs";
import { JobCard } from "@/features/jobs/ui/JobCard";
import { JobFiltersBar } from "@/features/jobs/ui/JobFilters";
import { SearchPanel } from "@/features/jobs/ui/SearchPanel";
import { Empty, ErrorNote, Skeleton } from "@/ui/primitives";
import { useSaveToggle } from "./useSaveToggle";

export function JobsPage() {
  const nav = useNavigate();
  const [filters, setFilters] = useState<JobFilters>({ sort: "score" });
  const jobs = useJobs(filters);
  const search = useSearch();
  const elapsed = useElapsed(search.startedAt, search.running);
  const save = useSaveToggle();
  return (
    <div className="stack-lg">
      <header className="page-head"><h1>Ofertas</h1><p className="muted">Filtra por rol, modalidad, salario y fecha, u ordénalas por encaje o por las más recientes. También puedes buscar un rol distinto al de tu CV.</p></header>
      <SearchPanel view={search.view} running={search.running} starting={search.starting} elapsed={elapsed} error={search.error} onStart={search.start} onDismiss={search.dismiss} onSeeJobs={() => window.scrollTo({ top: 9999, behavior: "smooth" })} />
      <ErrorNote error={jobs.error} />
      <JobFiltersBar value={filters} onChange={setFilters} />
      {jobs.isLoading ? <div className="jobs-grid">{[0, 1, 2].map((i) => <Skeleton key={i} lines={4} className="card" />)}</div> : jobs.data && jobs.data.items.length > 0 ? (
        <>
          <p className="muted small">{jobs.data.total} ofertas</p>
          <div className="jobs-grid">{jobs.data.items.map((it) => <JobCard key={it.id} item={it} onOpen={(id) => nav(`/jobs/${id}`)} onToggleSave={save} />)}</div>
        </>
      ) : <Empty icon={<Binoculars size={28} />} title="Aún no hay ofertas" hint="Pulsa «Buscar ahora»: recorremos los portales y empresas según tu perfil." />}
    </div>
  );
}
