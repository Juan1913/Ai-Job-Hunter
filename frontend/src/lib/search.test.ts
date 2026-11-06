import { describe, expect, it } from "vitest";
import { buildSearchView, clock, elapsedSeconds } from "./search";
import { computeMetrics } from "./metrics";
import type { JobOut } from "@/api/types";

describe("buildSearchView", () => {
  it("muestra el avance por fuente y la muestra de títulos", () => {
    const v = buildSearchView({ id: 1, status: "running", keywords: ["python"], sources: {
      computrabajo: { status: "done", found: 3, outcome: "ok", seconds: 4.2, sample: [{ title: "Dev", company: "Acme", location: "Bogotá", remote: false }] },
      linkedin: { status: "pending" }, magneto: { status: "error", error: "x", outcome: "blocked" },
    } });
    expect(v.phase).toBe("scanning");
    expect(v.doneSources).toBe(2);
    expect(v.totalSources).toBe(3);
    expect(v.found).toBe(3);
    expect(v.rows.find((r) => r.key === "magneto")?.state).toBe("blocked");
    expect(v.feed).toHaveLength(1);
    expect(v.progress).toBeGreaterThan(0);
    expect(v.progress).toBeLessThan(100);
    expect(v.keywords).toEqual(["python"]);
  });
  it("pasa por «guardando» cuando todas las fuentes respondieron y termina al 100%", () => {
    const src = { a: { status: "done", found: 0 } };
    expect(buildSearchView({ id: 1, status: "running", sources: src }).phase).toBe("saving");
    const done = buildSearchView({ id: 1, status: "done", sources: src, new_jobs: 5 });
    expect(done.phase).toBe("done");
    expect(done.progress).toBe(100);
    expect(done.newJobs).toBe(5);
  });
  it("en cola cuando aún no hay fuentes", () => expect(buildSearchView({ id: 1, status: "queued" }).phase).toBe("queued"));
});

describe("tiempo", () => {
  it("interpreta fechas sin zona como UTC y formatea mm:ss", () => {
    expect(elapsedSeconds("2026-01-01T00:00:00", Date.parse("2026-01-01T00:01:05Z"))).toBe(65);
    expect(clock(65)).toBe("1:05");
  });
});

const job = (o: Partial<JobOut> & { source?: string }): JobOut => ({ id: 1, status: "new", stage1: null, match: null, gaps: null, first_seen: "2026-01-05T00:00:00Z", evaluation: null, ai: null, job: { source: o.source ?? "linkedin", title: "t", company: "c" } as JobOut["job"], ...o });
describe("computeMetrics", () => {
  it("cuenta, promedia y agrupa", () => {
    const ev = (score: number, rec: string) => ({ score, recommendation: rec }) as JobOut["evaluation"];
    const m = computeMetrics([job({ evaluation: ev(4.5, "apply") }), job({ evaluation: ev(2.5, "skip"), status: "applied" }), job({ source: "magneto" })], new Date("2026-01-08T00:00:00Z"));
    expect(m.total).toBe(3);
    expect(m.fresh).toBe(3);
    expect(m.worthApplying).toBe(1);
    expect(m.avgScore).toBe(3.5);
    expect(m.pipeline.applied).toBe(1);
    expect(m.scoreBuckets.map((b) => b.count)).toEqual([0, 1, 0, 1]);
    expect(m.topSources[0]).toEqual({ name: "Linkedin", count: 2 });
  });
});
