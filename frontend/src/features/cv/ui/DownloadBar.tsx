import type { CvFormat, CvTemplate } from "@/api/types";
import { Button } from "@/ui/primitives";

const TEMPLATES: { id: CvTemplate; label: string }[] = [
  { id: "harvard", label: "Harvard" }, { id: "classic", label: "Clásica" }, { id: "compact", label: "Compacta" }, { id: "modern", label: "Moderna" },
];
export interface DownloadBarProps {
  template: CvTemplate;
  busy: boolean;
  onTemplate: (t: CvTemplate) => void;
  onDownload: (f: CvFormat) => void;
}
export function DownloadBar({ template, busy, onTemplate, onDownload }: DownloadBarProps) {
  return (
    <div className="row">
      <label className="inline">Formato
        <select value={template} onChange={(e) => onTemplate(e.target.value as CvTemplate)}>
          {TEMPLATES.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
        </select>
      </label>
      <Button variant="secondary" busy={busy} onClick={() => onDownload("pdf")}>Descargar PDF</Button>
      <Button variant="secondary" busy={busy} onClick={() => onDownload("docx")}>Descargar DOCX</Button>
    </div>
  );
}
