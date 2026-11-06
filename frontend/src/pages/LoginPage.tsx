import { useState } from "react";
import { Navigate } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { useAuth } from "@/features/auth/useAuth";
import { AuthForm } from "@/features/auth/ui/AuthForm";

export function LoginPage() {
  const { authenticated, signIn } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const m = useMutation({ mutationFn: (v: { email: string; password: string }) => signIn(v, mode) });
  if (authenticated) return <Navigate to="/" replace />;
  return <main className="center"><AuthForm mode={mode} busy={m.isPending} error={m.error} onSubmit={(email, password) => m.mutate({ email, password })} onToggleMode={() => { m.reset(); setMode(mode === "login" ? "register" : "login"); }} /></main>;
}
