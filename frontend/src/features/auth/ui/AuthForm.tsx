import { useState, type FormEvent } from "react";
import { Button, ErrorNote } from "@/ui/primitives";

export interface AuthFormProps {
  mode: "login" | "register";
  busy: boolean;
  error: unknown;
  onSubmit: (email: string, password: string) => void;
  onToggleMode: () => void;
}
export function AuthForm({ mode, busy, error, onSubmit, onToggleMode }: AuthFormProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const submit = (e: FormEvent) => { e.preventDefault(); onSubmit(email.trim(), password); };
  return (
    <form className="card auth" onSubmit={submit}>
      <div className="auth-brand"><span className="brand-mark" aria-hidden />JobHunter</div>
      <h1>{mode === "login" ? "Inicia sesión" : "Crea tu cuenta"}</h1>
      <p className="muted">Encuentra, puntúa y postula a las ofertas correctas. Nada se envía sin que tú lo decidas.</p>
      <label>Correo<input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
      <label>Contraseña{mode === "register" && <span className="muted small">Mínimo 10 caracteres</span>}<input type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} required minLength={10} value={password} onChange={(e) => setPassword(e.target.value)} /></label>
      <ErrorNote error={error} />
      <Button type="submit" busy={busy}>{mode === "login" ? "Entrar" : "Registrarme"}</Button>
      <Button type="button" variant="ghost" onClick={onToggleMode}>{mode === "login" ? "¿No tienes cuenta? Regístrate" : "Ya tengo cuenta"}</Button>
    </form>
  );
}
