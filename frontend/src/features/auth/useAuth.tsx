import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { getSession, onSessionExpired, setSession } from "@/api/http";
import type { Credentials, TokenOut } from "@/api/types";

interface AuthValue {
  authenticated: boolean;
  signIn: (c: Credentials, mode: "login" | "register") => Promise<void>;
  signOut: () => Promise<void>;
}
const Ctx = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authenticated, setAuth] = useState(() => getSession() !== null);
  const qc = useQueryClient();
  useEffect(() => { onSessionExpired(() => { qc.clear(); setAuth(false); }); }, [qc]);

  const signIn = useCallback(async (c: Credentials, mode: "login" | "register") => {
    const t: TokenOut = await (mode === "login" ? api.login(c) : api.register(c));
    setSession({ access: t.access_token, refresh: t.refresh_token ?? null });
    qc.clear();
    setAuth(true);
  }, [qc]);
  const signOut = useCallback(async () => {
    await api.logout(getSession()?.refresh ?? null);
    setSession(null);
    qc.clear();
    setAuth(false);
  }, [qc]);

  const value = useMemo(() => ({ authenticated, signIn, signOut }), [authenticated, signIn, signOut]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
export function useAuth(): AuthValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth fuera de <AuthProvider>");
  return v;
}
