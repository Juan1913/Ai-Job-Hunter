import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { JobFiltersBar } from "./JobFilters";
import { SearchBar } from "./SearchPanel";

describe("SearchBar", () => {
  it("permite buscar un rol propio con modalidad, país, contrato y antigüedad", async () => {
    const onStart = vi.fn();
    render(<SearchBar running={false} starting={false} error={null} onStart={onStart} />);
    await userEvent.type(screen.getByLabelText("Roles a buscar"), "chef, mesero");
    await userEvent.click(screen.getByRole("button", { name: /Opciones/ }));
    await userEvent.click(screen.getByRole("button", { name: "Remoto" }));
    await userEvent.selectOptions(screen.getByLabelText("País"), "MX");
    await userEvent.selectOptions(screen.getByLabelText("Publicada"), "72");
    await userEvent.click(screen.getByRole("button", { name: "Buscar ahora" }));
    expect(onStart).toHaveBeenCalledWith({ keywords: ["chef", "mesero"], workModes: ["remote"], countries: ["MX"], employmentTypes: undefined, hoursOld: 72 });
  });
  it("sin nada escrito busca con el perfil", async () => {
    const onStart = vi.fn();
    render(<SearchBar running={false} starting={false} error={null} onStart={onStart} />);
    await userEvent.click(screen.getByRole("button", { name: "Buscar ahora" }));
    expect(onStart).toHaveBeenCalledWith({ keywords: undefined, workModes: [], countries: undefined, employmentTypes: undefined, hoursOld: undefined });
  });
});

describe("JobFiltersBar", () => {
  it("cambia modalidad, orden y salario, y limpia", async () => {
    const onChange = vi.fn();
    render(<JobFiltersBar value={{ sort: "score", workMode: "remote" }} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Presencial" }));
    expect(onChange).toHaveBeenLastCalledWith({ sort: "score", workMode: "onsite", offset: 0 });
    await userEvent.selectOptions(screen.getByLabelText("Ordenar por"), "recent");
    expect(onChange).toHaveBeenLastCalledWith({ sort: "recent", workMode: "remote", offset: 0 });
    await userEvent.click(screen.getByRole("button", { name: /Más filtros/ }));
    await userEvent.type(screen.getByLabelText(/Salario anual mínimo/), "5");
    expect(onChange).toHaveBeenLastCalledWith({ sort: "score", workMode: "remote", minSalary: 5, offset: 0 });
    await userEvent.click(screen.getByRole("button", { name: /Limpiar/ }));
    expect(onChange).toHaveBeenLastCalledWith({ sort: "score" });
  });
});
