import type { components } from "./schema";

type S = components["schemas"];
/** El contrato marca como opcionales los campos con valor por defecto; el servidor SIEMPRE los envía. Los volvemos obligatorios (conservando null). */
type Req<T> = T extends (infer U)[] ? Req<U>[] : T extends object ? { [K in keyof T]-?: Req<T[K]> } : T;
export type Credentials = S["Credentials"];
export type TokenOut = S["TokenOut"];
export type Profile = Req<S["Profile"]>;
export type Job = Req<S["Job"]>;
export type JobOut = Req<S["JobOut"]>;
export type JobPage = Req<S["JobPage"]>;
export type JobStatus = S["JobStatus"];
export type Evaluation = Req<S["Evaluation"]>;
export type Dimension = Req<S["Dimension"]>;
export type KeywordGap = Req<S["KeywordGap"]>;
export type TailoredOut = Req<S["TailoredOut"]>;
export type TailoredCV = Req<S["TailoredCV"]>;
export type AtsReport = Req<S["AtsReport"]>;
export type SearchPreferences = S["SearchPreferences"];
export type Recommendation = "apply" | "consider" | "skip";
export type CvTemplate = "harvard" | "classic" | "compact" | "modern";
export type CvFormat = "pdf" | "docx";

/** Corrida de búsqueda (el contrato la deja sin tipar: es un dict). */
export interface SearchRun {
  id: number;
  status: string;
  sources?: unknown;
  new_jobs?: number;
  errors?: unknown;
  [k: string]: unknown;
}
export type WorkMode = "remote" | "hybrid" | "onsite";
export type EmploymentType = "full_time" | "part_time" | "contract" | "internship" | "temporary";
/** Opciones de UNA búsqueda: permiten buscar un rol distinto al del CV y acotar modalidad, país, contrato y antigüedad. */
export interface SearchOptions { keywords?: string[]; workModes?: WorkMode[]; countries?: string[]; employmentTypes?: EmploymentType[]; hoursOld?: number }
export interface JobFilters {
  status?: JobStatus;
  recommendation?: Recommendation;
  q?: string;
  minMatch?: number;
  sort?: "match" | "score" | "recent" | "salary";
  workMode?: WorkMode;
  employmentType?: EmploymentType;
  minSalary?: number;
  postedDays?: number;
  country?: string;
  limit?: number;
  offset?: number;
}

/* ---- Mapa ---- */
export interface MapJob { id: number; title: string; company: string; status: string; source: string; score: number | null; recommendation: string | null; remote: boolean }
export interface MapPoint { place: string; lat: number; lon: number; country: string; precision: "city" | "country"; count: number; best_score: number | null; jobs: MapJob[] }
export interface MapData { total: number; located: number; remote: number; unlocated: number; points: MapPoint[] }

/* ---- Seguimiento de postulaciones ---- */
export type Stage = "saved" | "applied" | "screening" | "interviewing" | "offer" | "closed";
export type Outcome = "offer" | "rejected" | "no_response" | "withdrawn";
export interface Reminder { kind: string; priority: string; due?: string | null; message?: string; [k: string]: unknown }
export interface Application {
  id: number; job_id: number; title: string | null; company: string | null; job_status: string | null; stage: Stage;
  applied_at: string | null; next_step: string | null; next_step_at: string | null; deadline_at: string | null;
  first_response_at: string | null; outcome: Outcome | null; outcome_reason: string | null; outcome_at: string | null;
  interviews: { id: string; kind: string; done: boolean; scheduled_at?: string | null }[]; notes: string | null; reminders: Reminder[]; updated_at: string | null;
}
export interface InsightCause { code: string; label: string; evidence: string; confidence: "high" | "medium" | "low" }
export interface InsightAction { id: string; priority: number; kind: string; title: string; detail: string; evidence: string[]; target: string; job_id: number | null }
export interface Diagnosis { job_id: number; title: string | null; company: string | null; outcome: Outcome | null; stage: string; applied_at: string | null; score: number | null; ats: number | null; causes: InsightCause[]; inconclusive: boolean }
export interface Insights {
  as_of: string; mode: "rules" | "llm"; headline: string; narrative: string | null; caveat: string; trace: string[];
  sample: { sent: number; negative: number; positive: number; enough: boolean };
  actions: InsightAction[]; diagnoses: Diagnosis[];
  to_apply: { job_id: number; title: string | null; company: string | null; deadline_at: string | null; score: number | null }[];
}

/* ---- Portales propios y auto-postulación ---- */
export interface Portal { id: number; name: string; kind: string; url: string | null; token: string | null; stealth: boolean; active: boolean; global: boolean }
export type ApplyMode = "off" | "approval" | "auto";
export type CvMode = "base" | "tailored";
export interface AutoApplyPolicy { mode: ApplyMode; paused: boolean; dry_run: boolean; min_score: number; only_recommended: boolean; daily_cap: number; cv_mode: CvMode; cover_letter: boolean; portal_modes: Record<string, ApplyMode> }
export interface ApplyQA { pattern: string; answer: string }
export interface ApplyData {
  full_name: string | null; email: string | null; phone: string | null; city: string | null; linkedin_url: string | null; github_url: string | null; portfolio_url: string | null;
  expected_salary: string | null; notice_period: string | null; answers: ApplyQA[]; accept_privacy_notices: boolean; decline_demographics: boolean;
}
export type AttemptStatus = "awaiting_approval" | "queued" | "running" | "submitted" | "dry_run" | "needs_manual" | "unconfirmed" | "failed" | "rejected";
export interface ApplyAttempt {
  id: number; job_id: number; title: string | null; company: string | null; source: string | null; status: AttemptStatus; trigger: "auto" | "manual" | "approved";
  adapter: string; cv_mode: CvMode; tailored_id: number | null; dry_run: boolean; error: string | null; apply_url: string | null; created_at: string; finished_at: string | null;
  detail: { message?: string; filled?: string[]; unanswered?: string[]; steps?: string[]; final_url?: string; cv_ats?: number };
}
export interface ApplyPreview { adapter: string | null; can_run: boolean; blockers: { code: string; message: string }[]; missing_data: string[]; cv_mode: CvMode; dry_run: boolean; apply_url: string; daily_left: number }
export interface AutoCapabilities { browser_available: boolean; automatable: string[]; manual_only_reason: string; reasons: Record<string, string> }

/* ---- Búsquedas programadas ---- */
export interface SavedSearchParams { keywords?: string[]; countries?: string[]; work_modes?: WorkMode[]; employment_types?: EmploymentType[]; hours_old?: number; remote?: boolean; limit_per_source?: number }
export interface SavedSearch {
  id: number; name: string; params: SavedSearchParams; interval_minutes: number; timezone: string; window_start: string | null; window_end: string | null; window_days: number[];
  active: boolean; paused_reason: string | null; next_run_at: string | null; last_run_at: string | null; last_run_id: number | null; failure_streak: number; total_runs: number; last_error: string | null;
}
export interface SavedSearchIn { name: string; keywords?: string[]; countries?: string[]; work_modes?: WorkMode[]; employment_types?: EmploymentType[]; hours_old?: number; interval_minutes: number; window_start?: string; window_end?: string; active?: boolean }
export interface ScheduleLimits { min_interval_minutes: number; max_per_user: number; pause_after_failures: number }
export interface WorkerStatus { alive: boolean; count: number }
