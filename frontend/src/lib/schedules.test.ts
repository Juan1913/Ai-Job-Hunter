import { describe, expect, it } from "vitest";
import { intervalLabel, paramChips, pausedWhy, whenLabel } from "./schedules";

describe("schedules", () => {
  const now = Date.parse("2026-01-01T12:00:00Z");
  it("describe intervalos y tiempos relativos", () => {
    expect(intervalLabel(360)).toBe("cada 6 horas");
    expect(intervalLabel(2880)).toBe("cada 2 días");
    expect(whenLabel("2026-01-01T14:00:00Z", now)).toBe("en 2 h");
    expect(whenLabel("2026-01-01T11:45:00Z", now)).toBe("hace 15 min");
    expect(whenLabel(null, now)).toBe("sin programar");
  });
  it("resume los filtros y explica por qué está en pausa", () => {
    expect(paramChips({ work_modes: ["remote", "hybrid"], countries: ["MX"], hours_old: 168 })).toEqual(["Remoto", "Híbrido", "MX", "Última semana"]);
    expect(pausedWhy({ paused_reason: "failures", last_error: "bloqueado" })).toMatch(/fallos seguidos: bloqueado/);
    expect(pausedWhy({ paused_reason: null, last_error: null })).toBe("Pausada por ti");
  });
});
