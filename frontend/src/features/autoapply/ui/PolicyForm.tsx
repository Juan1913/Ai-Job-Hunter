import { useEffect, useState } from "react";
import { WarningOctagon } from "@phosphor-icons/react";
import type { ApplyMode, AutoApplyPolicy, AutoCapabilities } from "@/api/types";
import { modeInfo, policySentence, portalKindLabel } from "@/lib/autoapply";
import { Button, Card, ErrorNote } from "@/ui/primitives";
import { ModePicker } from "./ModePicker";
import { Switch } from "./Switch";

export interface PolicyFormProps { policy: AutoApplyPolicy; caps: AutoCapabilities | undefined; busy: boolean; error: unknown; onSave: (p: AutoApplyPolicy) => void }

/** Cómo y cuándo postula el sistema. Todo lo que pasa por aquí lo decide el usuario; por defecto está apagado y en ensayo. */
export function PolicyForm({ policy, caps, busy, error, onSave }: PolicyFormProps) {
  const [d, setD] = useState(policy);
  useEffect(() => setD(policy), [policy]);
  const set = <K extends keyof AutoApplyPolicy>(k: K, v: AutoApplyPolicy[K]) => setD((x) => ({ ...x, [k]: v }));
  const dirty = JSON.stringify(d) !== JSON.stringify(policy);
  const setPortal = (kind: string, v: ApplyMode | "inherit") => setD((x) => { const pm = { ...x.portal_modes }; if (v === "inherit") delete pm[kind]; else pm[kind] = v; return { ...x, portal_modes: pm }; });
  return (
    <Card title="Cómo postula el sistema" subtitle={policySentence(d)} className="aa-policy">
      <ModePicker value={d.mode} onChange={(m) => set("mode", m)} />
      {d.mode !== "off" && (
        <div className="aa-grid">
          <Switch checked={d.dry_run} onChange={(v) => set("dry_run", v)} title="Modo ensayo" hint="Rellena el formulario como si fuera a enviarlo, pero NO lo envía. Recomendado hasta que veas que lo hace bien." />
          {!d.dry_run && <p className="aa-warn" role="note"><WarningOctagon size={18} weight="fill" /> Ensayo apagado: las postulaciones se enviarán de verdad a nombre tuyo.</p>}
          <Switch checked={d.paused} onChange={(v) => set("paused", v)} tone="warn" title="Pausa de emergencia" hint="Detiene todo al instante: no se prepara ni se envía nada." />
          <label className="aa-field"><span>Encaje mínimo: <b>{d.min_score.toFixed(1)}</b> de 5</span>
            <input type="range" min={2} max={5} step={0.1} value={d.min_score} onChange={(e) => set("min_score", Number(e.target.value))} />
          </label>
          <Switch checked={d.only_recommended} onChange={(v) => set("only_recommended", v)} title="Solo las que «valen la pena»" hint="Ignora ofertas que la evaluación no recomienda postular." />
          <label className="aa-field">Máximo por día
            <input type="number" min={1} max={30} value={d.daily_cap} onChange={(e) => set("daily_cap", Math.min(30, Math.max(1, Number(e.target.value) || 1)))} />
          </label>
          <fieldset className="aa-field aa-cv">
            <legend>CV que se envía</legend>
            <label className={d.cv_mode === "base" ? "is-on" : ""}><input type="radio" name="cv" checked={d.cv_mode === "base"} onChange={() => set("cv_mode", "base")} /><b>Mi CV general</b><span className="muted small">El mismo para todas, armado con todo tu perfil real.</span></label>
            <label className={d.cv_mode === "tailored" ? "is-on" : ""}><input type="radio" name="cv" checked={d.cv_mode === "tailored"} onChange={() => set("cv_mode", "tailored")} /><b>Uno hecho para cada puesto</b><span className="muted small">Se adapta a lo que pide la oferta, sin inventar nada que no tengas.</span></label>
          </fieldset>
          <Switch checked={d.cover_letter} onChange={(v) => set("cover_letter", v)} title="Incluir carta de presentación" hint="Si el formulario tiene campo para carta, se escribe una para ese puesto." />
        </div>
      )}
      {d.mode !== "off" && caps && (
        <div className="aa-portals">
          <h3>Por tipo de portal</h3>
          <p className="muted small">Puedes dar más o menos libertad según el portal. Los que no aparecen aquí (LinkedIn, Computrabajo, Indeed…) exigen iniciar sesión: ahí solo postulas tú.</p>
          <ul>
            {caps.automatable.map((k) => (
              <li key={k}><span>{portalKindLabel(k)}</span>
                <select aria-label={`Modo para ${portalKindLabel(k)}`} value={d.portal_modes[k] ?? "inherit"} onChange={(e) => setPortal(k, e.target.value as ApplyMode | "inherit")}>
                  <option value="inherit">Como arriba</option>
                  {(Object.keys(modeInfo) as ApplyMode[]).map((m) => <option key={m} value={m}>{modeInfo[m].label}</option>)}
                </select></li>
            ))}
          </ul>
          {!caps.browser_available && <p className="error">Este servidor no tiene el navegador necesario para postular.</p>}
        </div>
      )}
      <ErrorNote error={error} />
      <div className="row"><Button busy={busy} disabled={!dirty} onClick={() => onSave(d)}>Guardar cambios</Button>{dirty && <Button variant="ghost" onClick={() => setD(policy)}>Descartar</Button>}</div>
    </Card>
  );
}
