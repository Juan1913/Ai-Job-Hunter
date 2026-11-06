import { CheckCircle, WarningCircle } from "@phosphor-icons/react";

/** Dice con claridad si las búsquedas programadas se están ejecutando. */
export function WorkerBanner({ alive, loading }: { alive: boolean | undefined; loading: boolean }) {
  if (loading || alive === undefined) return null;
  return alive
    ? <p className="worker worker-ok" role="status"><CheckCircle size={18} weight="fill" /> El trabajador está activo: tus búsquedas programadas se ejecutan solas.</p>
    : <p className="worker worker-off" role="alert"><WarningCircle size={18} weight="fill" /> No hay un trabajador activo, así que las búsquedas programadas NO se ejecutan. Inicia todo con <code>./dev.sh</code> (incluye el trabajador) o ejecuta <code>python -m app.worker</code> en el backend.</p>;
}
