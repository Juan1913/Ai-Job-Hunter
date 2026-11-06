import { useRef, useState, type DragEvent } from "react";
import { Button, ErrorNote } from "@/ui/primitives";

export function UploadCv({ busy, error, onFile }: { busy: boolean; error: unknown; onFile: (f: File) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const drop = (e: DragEvent) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) onFile(f); };
  return (
    <div className={`drop ${over ? "drop-over" : ""}`} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={drop}>
      <p><strong>Sube tu CV</strong> (PDF o DOCX). Construimos tu perfil a partir de él; tú lo revisas.</p>
      <input ref={input} type="file" hidden accept=".pdf,.docx,application/pdf" onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); }} aria-label="Archivo del CV" />
      <Button busy={busy} onClick={() => input.current?.click()}>Elegir archivo</Button>
      <ErrorNote error={error} />
    </div>
  );
}
