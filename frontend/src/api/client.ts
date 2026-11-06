import { ApiError, download, request } from "./http";
import type { SavedSearch, SavedSearchIn, ScheduleLimits, WorkerStatus, SearchOptions, ApplyAttempt, ApplyData, ApplyPreview, AutoApplyPolicy, AutoCapabilities, CvMode, Portal, Application, Insights, MapData, Outcome, Stage, CvFormat, CvTemplate, Credentials, JobFilters, JobOut, JobPage, JobStatus, Profile, SearchPreferences, SearchRun, TailoredOut, TokenOut, Evaluation } from "./types";

const qs = (o: Record<string, string | number | undefined>): string => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(o)) if (v !== undefined && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
};

/** Todas las llamadas del backend, una función por endpoint. Es la única capa que conoce las URLs. */
export const api = {
  register: (c: Credentials) => request<TokenOut>("/auth/register", { method: "POST", json: c }),
  login: (c: Credentials) => request<TokenOut>("/auth/login", { method: "POST", json: c }),
  logout: (refresh: string | null) => request<void>("/auth/logout", { method: "POST", json: { refresh_token: refresh } }).catch(() => undefined),

  profile: () => request<Profile>("/me/profile"),
  uploadCv: (file: File) => { const f = new FormData(); f.append("file", file); return request<Profile>("/me/cv", { method: "POST", form: f }); },
  savePreferences: (p: Partial<SearchPreferences>) => request<Profile>("/me/preferences", { method: "PUT", json: p }),

  startSearch: (o: SearchOptions = {}) => request<SearchRun>("/me/searches", { method: "POST", json: {
    keywords: o.keywords ?? null, work_modes: o.workModes?.length ? o.workModes : null, countries: o.countries?.length ? o.countries : null,
    employment_types: o.employmentTypes?.length ? o.employmentTypes : null, hours_old: o.hoursOld ?? null } }),
  searchRun: (id: number) => request<SearchRun>(`/me/searches/${id}`),
  searchRuns: () => request<SearchRun[]>("/me/searches"),

  jobs: (f: JobFilters = {}) =>
    request<JobPage>(`/me/jobs${qs({ status: f.status, recommendation: f.recommendation, q: f.q, min_match: f.minMatch, sort: f.sort, work_mode: f.workMode, employment_type: f.employmentType, min_salary: f.minSalary, posted_days: f.postedDays, country: f.country, limit: f.limit ?? 30, offset: f.offset })}`),
  job: (id: number) => request<JobOut>(`/me/jobs/${id}`),
  evaluation: (id: number) => request<Evaluation>(`/me/jobs/${id}/evaluation`),
  setStatus: (id: number, status: JobStatus) => request<JobOut>(`/me/jobs/${id}`, { method: "PATCH", json: { status } }),
  /** «Aplicar»: el sistema NO envía nada; registra la intención y devuelve el destino. */
  openApply: (id: number) => request<{ url?: string; [k: string]: unknown }>(`/me/jobs/${id}/apply`, { method: "POST" }),

  aiStatus: () => request<{ ai_available: boolean; mode: string }>("/ai/status"),
  portals: () => request<Portal[]>("/me/companies"),
  addPortal: (p: { name: string; url: string; stealth: boolean }) => request<Portal>("/me/companies", { method: "POST", json: p }),
  deletePortal: (id: number) => request<void>(`/me/companies/${id}`, { method: "DELETE" }),

  autoCaps: () => request<AutoCapabilities>("/me/auto-apply/capabilities"),
  autoPolicy: () => request<AutoApplyPolicy>("/me/auto-apply/policy"),
  savePolicy: (p: AutoApplyPolicy) => request<AutoApplyPolicy>("/me/auto-apply/policy", { method: "PUT", json: p }),
  autoData: () => request<ApplyData>("/me/auto-apply/data"),
  saveData: (d: ApplyData) => request<ApplyData>("/me/auto-apply/data", { method: "PUT", json: d }),
  autoPreview: (jobId: number) => request<ApplyPreview>(`/me/auto-apply/jobs/${jobId}/preview`),
  /** «Postula por mí» para una oferta: exige confirmación explícita del usuario. */
  autoApplyNow: (jobId: number, o: { cv_mode?: CvMode; dry_run?: boolean }) => request<ApplyAttempt>(`/me/auto-apply/jobs/${jobId}`, { method: "POST", json: { confirm: true, ...o } }),
  attempts: (status?: string) => request<ApplyAttempt[]>(`/me/auto-apply/attempts${status ? `?status=${status}` : ""}`),
  approveAttempt: (id: number, dryRun?: boolean) => request<ApplyAttempt>(`/me/auto-apply/attempts/${id}/approve${dryRun === undefined ? "" : `?dry_run=${dryRun}`}`, { method: "POST" }),
  rejectAttempt: (id: number) => request<ApplyAttempt>(`/me/auto-apply/attempts/${id}/reject`, { method: "POST" }),
  retryAttempt: (id: number) => request<ApplyAttempt>(`/me/auto-apply/attempts/${id}/retry`, { method: "POST" }),

  schedules: () => request<SavedSearch[]>("/me/saved-searches"),
  scheduleLimits: () => request<ScheduleLimits>("/me/saved-searches/limits"),
  workerStatus: () => request<WorkerStatus>("/me/saved-searches/worker"),
  createSchedule: (b: SavedSearchIn) => request<SavedSearch>("/me/saved-searches", { method: "POST", json: b }),
  deleteSchedule: (id: number) => request<void>(`/me/saved-searches/${id}`, { method: "DELETE" }),
  pauseSchedule: (id: number) => request<SavedSearch>(`/me/saved-searches/${id}/pause`, { method: "POST" }),
  resumeSchedule: (id: number) => request<SavedSearch>(`/me/saved-searches/${id}/resume`, { method: "POST" }),
  runSchedule: (id: number) => request<unknown>(`/me/saved-searches/${id}/run`, { method: "POST" }),

  mapJobs: () => request<MapData>("/me/map/jobs"),

  applications: () => request<Application[]>("/me/applications?limit=500"),
  /** La candidatura de una oferta, o null si aún no se sigue. */
  application: (id: number) => request<Application>(`/me/jobs/${id}/application`).catch((e) => { if (e instanceof ApiError && e.status === 404) return null; throw e; }),
  startTracking: (id: number) => request<Application>(`/me/jobs/${id}/application`, { method: "PUT" }),
  /** El usuario declara que postuló por su cuenta: el sistema no envía nada. */
  markApplied: (id: number, appliedAt?: string) => request<Application>(`/me/jobs/${id}/application/apply`, { method: "POST", json: appliedAt ? { applied_at: appliedAt } : {} }),
  setStage: (id: number, stage: Stage) => request<Application>(`/me/jobs/${id}/application`, { method: "PATCH", json: { stage } }),
  setOutcome: (id: number, outcome: Outcome | null, reason?: string) => request<Application>(`/me/jobs/${id}/application/outcome`, { method: "PUT", json: { outcome, reason: reason || null } }),
  insights: (narrate = false) => request<Insights>(`/me/pipeline/insights${narrate ? "?narrate=true" : ""}`),

  tailor: (id: number, confirmed: string[] = []) => request<TailoredOut>(`/me/jobs/${id}/tailor`, { method: "POST", json: { confirmed, save_confirmed: false } }),
  cvs: (id: number) => request<TailoredOut[]>(`/me/jobs/${id}/cvs`),
  cvFile: (cvId: number, format: CvFormat, template: CvTemplate) => download(`/me/cvs/${cvId}.${format}${qs({ template })}`),
};
export type Api = typeof api;
