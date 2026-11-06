import { HandPointing, Lightning, Power } from "@phosphor-icons/react";
import type { ApplyMode } from "@/api/types";
import { modeInfo } from "@/lib/autoapply";

const ICON: Record<ApplyMode, JSX.Element> = { off: <Power size={22} />, approval: <HandPointing size={22} />, auto: <Lightning size={22} weight="fill" /> };
const ORDER: ApplyMode[] = ["off", "approval", "auto"];
/** Tres opciones grandes: apagada, con tu aprobación o automática. */
export function ModePicker({ value, onChange }: { value: ApplyMode; onChange: (m: ApplyMode) => void }) {
  return (
    <div className="aa-modes" role="radiogroup" aria-label="Modo de auto-postulación">
      {ORDER.map((m) => (
        <button key={m} type="button" role="radio" aria-checked={value === m} className={`aa-mode ${value === m ? "is-active" : ""} aa-mode-${m}`} onClick={() => onChange(m)}>
          <span className="aa-mode-icon">{ICON[m]}</span><b>{modeInfo[m].label}</b><span className="muted small">{modeInfo[m].hint}</span>
        </button>
      ))}
    </div>
  );
}
