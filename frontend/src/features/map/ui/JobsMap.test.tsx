import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { MapPoint } from "@/api/types";
import { JobsMap } from "./JobsMap";

const pt = (place: string, lon: number, lat: number, count: number, best: number | null, precision: "city" | "country" = "city"): MapPoint => ({ place, lon, lat, count, best_score: best, precision, country: "XX", jobs: [] });
describe("JobsMap (mapamundi SVG)", () => {
  it("dibuja una burbuja por lugar y avisa al hacer clic, sin pedir nada a la red", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const onSelect = vi.fn();
    render(<JobsMap points={[pt("Bogotá", -74, 4.7, 8, 4.2), pt("Colombia", -74, 4.5, 2, null, "country")]} selected={null} onSelect={onSelect} />);
    expect(screen.getByRole("application", { name: /Mapamundi/ })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /ofertas$/ })).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Bogotá: 8 ofertas" }));
    expect(onSelect).toHaveBeenCalledWith("Bogotá");
    expect(screen.getByRole("button", { name: "Acercar" })).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
