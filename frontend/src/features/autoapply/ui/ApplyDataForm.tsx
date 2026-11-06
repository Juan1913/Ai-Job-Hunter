import { useEffect, useState } from "react";
import { Plus, Trash } from "@phosphor-icons/react";
import type { ApplyData } from "@/api/types";
import { Button, Card, ErrorNote } from "@/ui/primitives";
import { Switch } from "./Switch";

const TEXT: { key: keyof ApplyData; label: string; placeholder: string; type?: string }[] = [
  { key: "full_name", label: "Nombre completo", placeholder: "Como en tu CV" }, { key: "email", label: "Correo", placeholder: "tu@correo.com", type: "email" },
  { key: "phone", label: "Teléfono", placeholder: "+57 300 000 0000" }, { key: "city", label: "Ciudad", placeholder: "Bogotá, Colombia" },
  { key: "linkedin_url", label: "LinkedIn", placeholder: "https://linkedin.com/in/…" }, { key: "github_url", label: "GitHub", placeholder: "https://github.com/…" },
  { key: "portfolio_url", label: "Portafolio o sitio web", placeholder: "https://…" }, { key: "expected_salary", label: "Pretensión salarial", placeholder: "Ej.: 8.000.000 COP" },
  { key: "notice_period", label: "Disponibilidad para empezar", placeholder: "Ej.: Inmediata" },
];
/** Los datos que se escriben en los formularios. Lo que no esté aquí ni en tu perfil, el sistema NO lo inventa: se detiene y te avisa. */
export function ApplyDataForm({ data, busy, error, onSave }: { data: ApplyData; busy: boolean; error: unknown; onSave: (d: ApplyData) => void }) {
  const [d, setD] = useState(data);
  useEffect(() => setD(data), [data]);
  const dirty = JSON.stringify(d) !== JSON.stringify(data);
  const setQA = (i: number, k: "pattern" | "answer", v: string) => setD((x) => ({ ...x, answers: x.answers.map((a, j) => (j === i ? { ...a, [k]: v } : a)) }));
  return (
    <Card title="Mis datos para postular" subtitle="Lo que escribe el sistema en los formularios. Se completa con tu perfil; aquí lo ajustas.">
      <div className="aa-data">
        {TEXT.map((f) => <label key={f.key} className="aa-field">{f.label}<input type={f.type ?? "text"} value={(d[f.key] as string | null) ?? ""} placeholder={f.placeholder} onChange={(e) => setD({ ...d, [f.key]: e.target.value || null })} /></label>)}
      </div>
      <h3>Respuestas fijas a preguntas frecuentes</h3>
      <p className="muted small">Si la pregunta del formulario contiene el texto de la izquierda, se responde lo de la derecha. Ej.: «años de experiencia con python» → «6».</p>
      <ul className="aa-qa">
        {d.answers.map((a, i) => (
          <li key={i}>
            <input aria-label="Si la pregunta contiene…" placeholder="Si la pregunta contiene…" value={a.pattern} onChange={(e) => setQA(i, "pattern", e.target.value)} />
            <input aria-label="Responder…" placeholder="Responder…" value={a.answer} onChange={(e) => setQA(i, "answer", e.target.value)} />
            <button type="button" className="icon-btn" aria-label="Quitar respuesta" onClick={() => setD({ ...d, answers: d.answers.filter((_, j) => j !== i) })}><Trash size={18} /></button>
          </li>
        ))}
      </ul>
      <Button variant="secondary" icon={<Plus size={16} />} onClick={() => setD({ ...d, answers: [...d.answers, { pattern: "", answer: "" }] })}>Añadir respuesta</Button>
      <div className="aa-grid">
        <Switch checked={d.accept_privacy_notices} onChange={(v) => setD({ ...d, accept_privacy_notices: v })} title="Aceptar avisos de privacidad" hint="Marca las casillas «acepto la política de privacidad / tratamiento de datos» que casi todos los formularios exigen. Nunca las de marketing." />
        <Switch checked={d.decline_demographics} onChange={(v) => setD({ ...d, decline_demographics: v })} title="No responder preguntas demográficas" hint="En las voluntarias (género, etnia…) elige «prefiero no decir» si existe esa opción." />
      </div>
      <ErrorNote error={error} />
      <div className="row"><Button busy={busy} disabled={!dirty} onClick={() => onSave({ ...d, answers: d.answers.filter((a) => a.pattern.trim() && a.answer.trim()) })}>Guardar mis datos</Button></div>
    </Card>
  );
}
