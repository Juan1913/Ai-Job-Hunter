/** Gráficos mínimos en HTML/CSS: legibles, sin librerías, mismos colores en claro y oscuro. */
export function Histogram({ data, label }: { data: { label: string; count: number }[]; label: string }) {
  const max = Math.max(1, ...data.map((d) => d.count));
  if (data.every((d) => d.count === 0)) return <p className="muted small">Aún no hay ofertas evaluadas. Abre una oferta para ver su puntaje.</p>;
  return (
    <div className="histo" role="img" aria-label={label}>
      {data.map((d) => (
        <div key={d.label} className="histo-col">
          <span className="histo-n">{d.count}</span>
          <span className="histo-bar" style={{ height: `${Math.max(4, (d.count / max) * 100)}%` }} />
          <span className="muted small">{d.label}</span>
        </div>
      ))}
    </div>
  );
}
export function BarList({ data, empty }: { data: { name: string; count: number }[]; empty: string }) {
  const max = Math.max(1, ...data.map((d) => d.count));
  if (data.length === 0) return <p className="muted small">{empty}</p>;
  return (
    <ul className="barlist">
      {data.map((d) => (
        <li key={d.name}><span className="barlist-name">{d.name}</span><span className="barlist-track"><span style={{ width: `${(d.count / max) * 100}%` }} /></span><b>{d.count}</b></li>
      ))}
    </ul>
  );
}
export function Funnel({ steps }: { steps: { label: string; count: number }[] }) {
  return (
    <ol className="funnel">
      {steps.map((s) => <li key={s.label}><b>{s.count}</b><span className="muted small">{s.label}</span></li>)}
    </ol>
  );
}
