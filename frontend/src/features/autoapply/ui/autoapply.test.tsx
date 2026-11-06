import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ApplyAttempt, ApplyPreview, AutoApplyPolicy } from "@/api/types";
import { ApplyForMePanel } from "./ApplyForMePanel";
import { AttemptList } from "./AttemptList";
import { PolicyForm } from "./PolicyForm";

const policy: AutoApplyPolicy = { mode: "off", paused: false, dry_run: true, min_score: 3.8, only_recommended: true, daily_cap: 5, cv_mode: "tailored", cover_letter: false, portal_modes: {} };
const caps = { browser_available: true, automatable: ["lever", "greenhouse"], manual_only_reason: "", reasons: {} };

describe("PolicyForm", () => {
  it("apagada por defecto: no muestra opciones de envío y guarda el cambio de modo", async () => {
    const onSave = vi.fn();
    render(<PolicyForm policy={policy} caps={caps} busy={false} error={null} onSave={onSave} />);
    expect(screen.queryByText("Modo ensayo")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: /Con tu aprobación/ }));
    expect(screen.getByText("Modo ensayo")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ mode: "approval", dry_run: true }));
  });
  it("avisa con claridad cuando se apaga el ensayo (envíos reales)", async () => {
    render(<PolicyForm policy={{ ...policy, mode: "auto" }} caps={caps} busy={false} error={null} onSave={vi.fn()} />);
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("switch", { name: /Modo ensayo/ }));
    expect(screen.getByRole("note")).toHaveTextContent(/se enviarán de verdad/);
  });
  it("ofrece modo por tipo de portal solo para los automatizables", async () => {
    render(<PolicyForm policy={{ ...policy, mode: "auto" }} caps={caps} busy={false} error={null} onSave={vi.fn()} />);
    expect(screen.getByLabelText("Modo para Lever")).toBeInTheDocument();
    expect(screen.queryByLabelText("Modo para LinkedIn")).not.toBeInTheDocument();
  });
});

const prev: ApplyPreview = { adapter: "lever", can_run: true, blockers: [], missing_data: [], cv_mode: "tailored", dry_run: true, apply_url: "u", daily_left: 4 };
describe("ApplyForMePanel", () => {
  it("exige abrir la confirmación y manda las opciones elegidas", async () => {
    const onApply = vi.fn();
    render(<ApplyForMePanel preview={prev} attempt={undefined} busy={false} error={null} onApply={onApply} />);
    expect(onApply).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /Postular por mí/ }));
    await userEvent.click(screen.getByLabelText(/Solo ensayo/));          // desmarca el ensayo
    await userEvent.click(screen.getByRole("radio", { name: /Mi CV general/ }));
    await userEvent.click(screen.getByRole("button", { name: "Confirmo: postular ahora" }));
    expect(onApply).toHaveBeenCalledWith({ cv_mode: "base", dry_run: false });
  });
  it("si no se puede, explica por qué y no ofrece el botón", () => {
    render(<ApplyForMePanel preview={{ ...prev, can_run: false, adapter: null, blockers: [{ code: "manual_only", message: "Este portal exige iniciar sesión." }] }} attempt={undefined} busy={false} error={null} onApply={vi.fn()} />);
    expect(screen.getByText(/exige iniciar sesión/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Postular por mí/ })).not.toBeInTheDocument();
  });
});

const att = (o: Partial<ApplyAttempt>): ApplyAttempt => ({ id: 1, job_id: 9, title: "Dev", company: "Acme", source: "lever:acme", status: "awaiting_approval", trigger: "auto", adapter: "lever", cv_mode: "tailored", tailored_id: null, dry_run: false, error: null, apply_url: "https://x", created_at: "2026-01-01T10:00:00Z", finished_at: null, detail: {}, ...o });
describe("AttemptList", () => {
  it("lo que espera aprobación no se envía hasta pulsar Aprobar", async () => {
    const onApprove = vi.fn();
    render(<AttemptList attempts={[att({})]} busy={false} onApprove={onApprove} onReject={vi.fn()} onRetry={vi.fn()} onOpenJob={vi.fn()} />);
    expect(onApprove).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Aprobar" }));
    expect(onApprove).toHaveBeenCalledWith(1);
  });
  it("cuando necesita al usuario, dice qué falta y deja abrir el portal", () => {
    render(<AttemptList attempts={[att({ status: "needs_manual", detail: { message: "No invento respuestas", unanswered: ["¿Por qué nosotros?"] } })]} busy={false} onApprove={vi.fn()} onReject={vi.fn()} onRetry={vi.fn()} onOpenJob={vi.fn()} />);
    expect(screen.getByText("¿Por qué nosotros?")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /terminar yo/ })).toHaveAttribute("href", "https://x");
  });
});
