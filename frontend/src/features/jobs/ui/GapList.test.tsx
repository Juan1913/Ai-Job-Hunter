import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { GapList } from "./GapList";

const gaps = [
  { keyword: "SIEM", status: "have", evidence: "Splunk", confirmed: false },
  { keyword: "AWS", status: "missing", evidence: null, confirmed: false },
] as never;

describe("GapList", () => {
  it("solo ofrece confirmar lo que falta, nunca lo que ya tienes", async () => {
    const onToggle = vi.fn();
    render(<GapList gaps={gaps} confirmed={new Set()} onToggle={onToggle} />);
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes).toHaveLength(1);
    await userEvent.click(boxes[0]!);
    expect(onToggle).toHaveBeenCalledWith("AWS");
  });
  it("refleja lo confirmado y recuerda la regla de oro", () => {
    render(<GapList gaps={gaps} confirmed={new Set(["AWS"])} onToggle={() => {}} />);
    expect(screen.getByRole("checkbox")).toBeChecked();
    expect(screen.getByText(/Nunca agregamos al CV nada que no tengas/)).toBeInTheDocument();
  });
});
