import { describe, expect, it } from "vitest";
import { atsTone, formatSalary, initialsOf, relativeDays, scoreTone, sourceName } from "./format";

describe("format", () => {
  it("clasifica puntajes", () => {
    expect([scoreTone(4.2), scoreTone(3.3), scoreTone(2), scoreTone(null)]).toEqual(["good", "warn", "bad", "neutral"]);
    expect([atsTone(90), atsTone(75), atsTone(40)]).toEqual(["good", "warn", "bad"]);
  });
  it("formatea salario con rango y moneda", () => {
    const s = formatSalary({ salary: null, salary_min: 8_000_000, salary_max: 10_000_000, salary_currency: "COP", salary_period: "month" });
    expect(s).toContain("COP");
    expect(s).toContain("/ month");
    expect(formatSalary({ salary: "A convenir", salary_min: null, salary_max: null, salary_currency: null, salary_period: null })).toBe("A convenir");
    expect(formatSalary({ salary: null, salary_min: null, salary_max: null, salary_currency: null, salary_period: null })).toBeNull();
  });
  it("nombra la fuente y la antigüedad", () => {
    expect(sourceName("greenhouse:mercadolibre")).toBe("Greenhouse · mercadolibre");
    expect(sourceName("computrabajo")).toBe("Computrabajo");
    const now = new Date("2026-11-05T12:00:00Z");
    expect([relativeDays("2026-11-05T01:00:00Z", now), relativeDays("2026-11-04T01:00:00Z", now), relativeDays("2026-11-01T00:00:00Z", now), relativeDays(null, now)]).toEqual(["hoy", "ayer", "hace 4 días", null]);
  });
  it("iniciales", () => { expect(initialsOf("Juan Torres Pérez")).toBe("JT"); expect(initialsOf(null)).toBe("?"); });
});

import { text } from "./format";
describe("text", () => {
  it("decodifica entidades HTML que traen algunas fuentes", () => {
    expect(text("Smart Solutions &amp; Services SA")).toBe("Smart Solutions & Services SA");
    expect(text(null)).toBe("");
  });
});
