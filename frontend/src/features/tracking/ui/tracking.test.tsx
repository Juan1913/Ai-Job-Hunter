import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Application, Insights } from "@/api/types";
import { ApplicationCard } from "./ApplicationCard";
import { InsightsPanel } from "./InsightsPanel";

const base: Application = { id: 1, job_id: 7, title: "Dev Backend", company: "Acme", job_status: null, stage: "saved", applied_at: null, next_step: null, next_step_at: null, deadline_at: null, first_response_at: null, outcome: null, outcome_reason: null, outcome_at: null, interviews: [], notes: null, reminders: [], updated_at: null };
const handlers = { busy: false, onOpen: vi.fn(), onMarkApplied: vi.fn(), onInterview: vi.fn(), onOutcome: vi.fn() };

describe("ApplicationCard", () => {
  it("dice que aún no postulaste y deja marcarlo, sin abrir la oferta", async () => {
    render(<ApplicationCard app={base} {...handlers} />);
    expect(screen.getByText("Aún no postulaste")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Ya postulé/ }));
    expect(handlers.onMarkApplied).toHaveBeenCalledWith(7);
    expect(handlers.onOpen).not.toHaveBeenCalled();
  });
  it("registra un rechazo con su motivo", async () => {
    render(<ApplicationCard app={{ ...base, stage: "applied", applied_at: new Date().toISOString() }} {...handlers} />);
    expect(screen.getByText("Postulaste hoy")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Registrar resultado" }));
    await userEvent.type(screen.getByLabelText(/Motivo/), "Más experiencia");
    await userEvent.click(screen.getByRole("button", { name: "Guardar resultado" }));
    expect(handlers.onOutcome).toHaveBeenCalledWith(7, "rejected", "Más experiencia");
  });
  it("una cerrada muestra el resultado y el motivo, y se puede reabrir", async () => {
    render(<ApplicationCard app={{ ...base, stage: "closed", applied_at: "2026-01-01", outcome: "rejected", outcome_reason: "Faltó Kubernetes" }} {...handlers} />);
    expect(screen.getByText("Rechazada")).toBeInTheDocument();
    expect(screen.getByText(/Faltó Kubernetes/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reabrir" }));
    expect(handlers.onOutcome).toHaveBeenCalledWith(7, null, "");
  });
});

const insights: Insights = {
  as_of: "", mode: "rules", headline: "2 de 3 postulaciones no avanzaron.", narrative: null, caveat: "Son hipótesis.", trace: ["Observé 3 candidaturas."], sample: { sent: 3, negative: 2, positive: 0, enough: true },
  actions: [{ id: "skill:kubernetes", priority: 1, kind: "skills", title: "«kubernetes» aparece en 2 de tus 2 candidaturas sin éxito", detail: "Añádelo si lo has usado.", evidence: ["Faltaba en 2 ofertas"], target: "profile", job_id: null }],
  diagnoses: [{ job_id: 7, title: "Dev Backend", company: "Acme", outcome: "rejected", stage: "closed", applied_at: null, score: 2.4, ats: null, inconclusive: false, causes: [{ code: "weak_fit", label: "Postulaste con poco encaje", evidence: "Puntaje 2.4/5", confidence: "medium" }] }], to_apply: [],
};
describe("InsightsPanel", () => {
  it("muestra acciones y causas con su evidencia y confianza, y avisa que son hipótesis", async () => {
    const onAction = vi.fn();
    render(<InsightsPanel insights={insights} loading={false} narrating={false} aiAvailable={false} onNarrate={vi.fn()} onAction={onAction} onOpenJob={vi.fn()} />);
    expect(screen.getByText(/kubernetes/)).toBeInTheDocument();
    expect(screen.getByText("Postulaste con poco encaje")).toBeInTheDocument();
    expect(screen.getByText("Respaldo medio")).toBeInTheDocument();
    expect(screen.getByText("Son hipótesis.")).toBeInTheDocument();
    expect(screen.queryByText("Redactar con IA")).not.toBeInTheDocument();      // sin IA no se ofrece
    await userEvent.click(screen.getByRole("button", { name: /Ir:/ }));
    expect(onAction).toHaveBeenCalledWith(insights.actions[0]);
  });
});
