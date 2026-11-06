/** Línea mínima de tendencia (sin ejes): sirve para ver el ritmo de un vistazo. */
export function Sparkline({ data, label }: { data: readonly number[]; label: string }) {
  if (data.length < 2 || data.every((d) => d === 0)) return null;
  const W = 96, H = 34, max = Math.max(...data, 1);
  const pts = data.map((v, i) => [(i / (data.length - 1)) * W, H - 3 - (v / max) * (H - 8)] as const);
  const line = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  return (
    <svg className="spark" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} preserveAspectRatio="none">
      <path className="area" d={`${line} L${W} ${H} L0 ${H} Z`} /><path className="line" d={line} />
    </svg>
  );
}
