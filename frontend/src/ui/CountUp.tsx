import { useEffect, useRef, useState } from "react";

/** Número que sube de 0 a `value` al aparecer (sin animación si el usuario prefiere menos movimiento). */
export function CountUp({ value, decimals = 0, ms = 700 }: { value: number; decimals?: number; ms?: number }) {
  const [shown, setShown] = useState(0);
  const from = useRef(0);
  useEffect(() => {
    const reduce = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduce || ms <= 0) { setShown(value); from.current = value; return; }
    const start = performance.now(), a = from.current;
    let raf = 0;
    const tick = (now: number) => {
      const p = Math.min(1, (now - start) / ms), e = 1 - Math.pow(1 - p, 3);
      setShown(a + (value - a) * e);
      if (p < 1) raf = requestAnimationFrame(tick); else from.current = value;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, ms]);
  return <>{decimals ? shown.toFixed(decimals) : Math.round(shown)}</>;
}
