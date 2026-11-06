import { describe, expect, it } from "vitest";
import { appliedState, columnOf, groupByColumn } from "./tracking";
import type { Application } from "@/api/types";

const app = (o: Partial<Application>): Application => ({ id: 1, job_id: 1, title: "t", company: "c", job_status: null, stage: "saved", applied_at: null, next_step: null, next_step_at: null, deadline_at: null, first_response_at: null, outcome: null, outcome_reason: null, outcome_at: null, interviews: [], notes: null, reminders: [], updated_at: null, ...o });
describe("seguimiento", () => {
  it("coloca cada candidatura en su columna", () => {
    expect(columnOf(app({}))).toBe("todo");
    expect(columnOf(app({ stage: "applied", applied_at: "2026-01-01" }))).toBe("applied");
    expect(columnOf(app({ stage: "interviewing", applied_at: "2026-01-01" }))).toBe("process");
    expect(columnOf(app({ stage: "closed", outcome: "rejected", applied_at: "2026-01-01" }))).toBe("closed");
    expect(columnOf(app({ outcome: "offer", stage: "offer" }))).toBe("offer");
    expect(groupByColumn([app({}), app({ id: 2, outcome: "no_response" })]).closed).toHaveLength(1);
  });
  it("dice con claridad si ya postulaste", () => {
    expect(appliedState({ applied_at: null, outcome: null })).toMatchObject({ applied: false, label: "Aún no postulaste" });
    const now = Date.parse("2026-01-10T00:00:00Z");
    expect(appliedState({ applied_at: "2026-01-05T00:00:00Z", outcome: null }, now).label).toBe("Postulaste hace 5 días");
    expect(appliedState({ applied_at: "2026-01-10T00:00:00Z", outcome: null }, now).label).toBe("Postulaste hoy");
  });
});
