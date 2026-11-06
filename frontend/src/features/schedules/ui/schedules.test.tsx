import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { SavedSearch } from "@/api/types";
import { ScheduleForm } from "./ScheduleForm";
import { ScheduleList } from "./ScheduleList";
import { WorkerBanner } from "./WorkerBanner";

describe("ScheduleForm", () => {
  it("crea una búsqueda con rol propio, filtros, frecuencia y horario", async () => {
    const onCreate = vi.fn();
    render(<ScheduleForm limits={{ min_interval_minutes: 60, max_per_user: 10, pause_after_failures: 5 }} busy={false} error={null} onCreate={onCreate} />);
    await userEvent.type(screen.getByLabelText("Nombre"), "Backend remoto");
    await userEvent.type(screen.getByLabelText("Roles a buscar"), "python, go");
    await userEvent.click(screen.getByRole("button", { name: "Remoto" }));
    await userEvent.selectOptions(screen.getByLabelText("País"), "CO");
    await userEvent.selectOptions(screen.getByLabelText("Frecuencia"), "60");
    await userEvent.click(screen.getByLabelText("Solo en este horario"));
    await userEvent.click(screen.getByRole("button", { name: "Programar" }));
    expect(onCreate).toHaveBeenCalledWith({ name: "Backend remoto", interval_minutes: 60, keywords: ["python", "go"], work_modes: ["remote"], countries: ["CO"], employment_types: undefined, hours_old: undefined, window_start: "08:00", window_end: "20:00" });
  });
  it("no ofrece frecuencias por debajo del mínimo permitido", () => {
    render(<ScheduleForm limits={{ min_interval_minutes: 360, max_per_user: 10, pause_after_failures: 5 }} busy={false} error={null} onCreate={vi.fn()} />);
    expect(screen.queryByRole("option", { name: "Cada hora" })).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Cada 6 horas" })).toBeInTheDocument();
  });
});

const base: SavedSearch = { id: 1, name: "Mi búsqueda", params: { keywords: ["python"], work_modes: ["remote"] }, interval_minutes: 360, timezone: "UTC", window_start: null, window_end: null, window_days: [], active: true, paused_reason: null, next_run_at: null, last_run_at: null, last_run_id: null, failure_streak: 0, total_runs: 0, last_error: null };
describe("ScheduleList", () => {
  const h = { busy: false, onRun: vi.fn(), onPause: vi.fn(), onResume: vi.fn(), onDelete: vi.fn() };
  it("ejecuta, pausa y exige confirmar antes de eliminar", async () => {
    render(<ScheduleList items={[base]} {...h} />);
    expect(screen.getByText(/Busca python/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Ejecutar ahora/ }));
    expect(h.onRun).toHaveBeenCalledWith(1);
    await userEvent.click(screen.getByRole("button", { name: /Pausar/ }));
    expect(h.onPause).toHaveBeenCalledWith(1);
    await userEvent.click(screen.getByRole("button", { name: "Eliminar Mi búsqueda" }));
    expect(h.onDelete).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Sí, eliminar" }));
    expect(h.onDelete).toHaveBeenCalledWith(1);
  });
  it("una pausada por fallos dice por qué y se puede reanudar", async () => {
    render(<ScheduleList items={[{ ...base, active: false, paused_reason: "failures", last_error: "bloqueado" }]} {...h} />);
    expect(screen.getByText(/fallos seguidos: bloqueado/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Reanudar/ }));
    expect(h.onResume).toHaveBeenCalledWith(1);
  });
});
describe("WorkerBanner", () => {
  it("avisa con claridad cuando no hay trabajador", () => {
    render(<WorkerBanner alive={false} loading={false} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/NO se ejecutan/);
  });
});
