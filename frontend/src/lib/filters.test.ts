import { describe, expect, it } from "vitest";
import { activeFilterCount } from "./filters";

describe("activeFilterCount", () => {
  it("cuenta filtros activos y no el orden", () => {
    expect(activeFilterCount({ sort: "recent" })).toBe(0);
    expect(activeFilterCount({ q: "python", workMode: "remote", minSalary: 5, postedDays: 7, sort: "recent" })).toBe(4);
  });
});
