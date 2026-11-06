import { useState, type FormEvent } from "react";
import { Plus } from "@phosphor-icons/react";
import { Button, Card, ErrorNote } from "@/ui/primitives";

export function AddPortalForm({ busy, error, onAdd }: { busy: boolean; error: unknown; onAdd: (p: { name: string; url: string; stealth: boolean }) => void }) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [stealth, setStealth] = useState(false);
  const submit = (e: FormEvent) => { e.preventDefault(); onAdd({ name: name.trim(), url: url.trim(), stealth }); setName(""); setUrl(""); };
  return (
    <Card title="Añadir un portal o página de empleo" subtitle="Detectamos solos si es Greenhouse, Lever, Ashby, Workable… o una página de empleo normal.">
      <form className="pt-form" onSubmit={submit}>
        <label className="aa-field">Nombre<input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Ej.: Rappi" /></label>
        <label className="aa-field">Enlace de sus ofertas<input required type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://jobs.lever.co/rappi" /></label>
        <label className="check"><input type="checkbox" checked={stealth} onChange={(e) => setStealth(e.target.checked)} />Página con protección anti-bots (usar navegador sigiloso)</label>
        <ErrorNote error={error} />
        <div><Button type="submit" busy={busy} icon={<Plus size={16} />}>Añadir</Button></div>
      </form>
    </Card>
  );
}
