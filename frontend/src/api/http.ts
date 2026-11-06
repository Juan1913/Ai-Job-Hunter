/** Cliente HTTP mínimo: token en memoria + localStorage, renovación automática con el refresh token y errores tipados. */
const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api/v1";
const KEY = "jh.session";

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) {
    super(message);
  }
}

export interface Session { access: string; refresh: string | null }
let session: Session | null = readSession();
let onExpired: (() => void) | null = null;

function readSession(): Session | null {
  try { const raw = localStorage.getItem(KEY); return raw ? (JSON.parse(raw) as Session) : null; } catch { return null; }
}
export function setSession(s: Session | null): void {
  session = s;
  try { if (s) localStorage.setItem(KEY, JSON.stringify(s)); else localStorage.removeItem(KEY); } catch { /* modo privado: solo en memoria */ }
}
export const getSession = (): Session | null => session;
export const onSessionExpired = (fn: () => void): void => { onExpired = fn; };

function message(status: number, body: unknown): string {
  const d = (body as { detail?: unknown } | null)?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((x) => (x as { msg?: string }).msg ?? "").filter(Boolean).join("; ") || `Error ${status}`;
  return `Error ${status}`;
}

let refreshing: Promise<boolean> | null = null;
async function refresh(): Promise<boolean> {
  if (!session?.refresh) return false;
  refreshing ??= (async () => {
    try {
      const r = await fetch(`${BASE}/auth/refresh`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: session!.refresh }) });
      if (!r.ok) return false;
      const t = (await r.json()) as { access_token: string; refresh_token?: string | null };
      setSession({ access: t.access_token, refresh: t.refresh_token ?? session!.refresh });
      return true;
    } catch { return false; }
  })().finally(() => { refreshing = null; });   // las llamadas simultáneas comparten UNA renovación; al terminar se libera
  return refreshing;
}

async function raw(path: string, init: RequestInit & { json?: unknown; form?: FormData } = {}, retry = true): Promise<Response> {
  const headers = new Headers(init.headers);
  if (session) headers.set("Authorization", `Bearer ${session.access}`);
  let body: BodyInit | undefined = init.body ?? undefined;
  if (init.json !== undefined) { headers.set("Content-Type", "application/json"); body = JSON.stringify(init.json); }
  if (init.form) body = init.form;                       // el navegador pone el boundary
  const res = await fetch(`${BASE}${path}`, { ...init, headers, body });
  if (res.status === 401 && retry && !path.startsWith("/auth/")) {
    if (await refresh()) return raw(path, init, false);
    setSession(null);
    onExpired?.();
  }
  return res;
}

export async function request<T>(path: string, init: Parameters<typeof raw>[1] = {}): Promise<T> {
  const res = await raw(path, init);
  const text = await res.text();
  const body = text ? safeJson(text) : null;
  if (!res.ok) throw new ApiError(res.status, message(res.status, body), body);
  return body as T;
}
export async function download(path: string): Promise<Blob> {
  const res = await raw(path);
  if (!res.ok) throw new ApiError(res.status, `No se pudo descargar (${res.status})`);
  return res.blob();
}
function safeJson(t: string): unknown { try { return JSON.parse(t); } catch { return t; } }
