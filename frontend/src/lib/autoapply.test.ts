import { describe, expect, it } from "vitest";
import { canAutomate, isActive, policySentence } from "./autoapply";

describe("autoapply", () => {
  const base = { mode: "auto" as const, paused: false, dry_run: false, min_score: 3.8, daily_cap: 5, cv_mode: "tailored" };
  it("resume la política con claridad y avisa del ensayo", () => {
    expect(policySentence({ ...base, mode: "off" })).toMatch(/no postula/);
    expect(policySentence({ ...base, paused: true })).toMatch(/pausa/);
    expect(policySentence({ ...base, dry_run: true })).toMatch(/ENSAYO/);
    expect(policySentence(base)).toMatch(/Postula sola.*3\.8.*5 al día.*cada puesto/);
    expect(policySentence({ ...base, mode: "approval", cv_mode: "base" })).toMatch(/aprobación.*CV general/);
  });
  it("distingue portales automatizables y estados activos", () => {
    expect(canAutomate("lever", ["lever", "ashby"])).toBe(true);
    expect(canAutomate("linkedin", ["lever"])).toBe(false);
    expect(isActive("running")).toBe(true);
    expect(isActive("submitted")).toBe(false);
  });
});
