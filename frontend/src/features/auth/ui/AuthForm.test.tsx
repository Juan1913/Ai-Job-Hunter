import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AuthForm } from "./AuthForm";

describe("AuthForm", () => {
  it("envía correo y contraseña y muestra el error recibido", async () => {
    const onSubmit = vi.fn();
    const { rerender } = render(<AuthForm mode="login" busy={false} error={null} onSubmit={onSubmit} onToggleMode={() => {}} />);
    await userEvent.type(screen.getByLabelText("Correo"), " a@b.co ");
    await userEvent.type(screen.getByLabelText("Contraseña"), "supersecreta");
    await userEvent.click(screen.getByRole("button", { name: "Entrar" }));
    expect(onSubmit).toHaveBeenCalledWith("a@b.co", "supersecreta");
    rerender(<AuthForm mode="register" busy={false} error={new Error("Correo ya registrado")} onSubmit={onSubmit} onToggleMode={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Correo ya registrado");
  });
});
