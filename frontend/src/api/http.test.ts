import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, getSession, request, setSession } from "./http";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("http", () => {
  beforeEach(() => { localStorage.clear(); setSession({ access: "old", refresh: "r1" }); });
  afterEach(() => vi.restoreAllMocks());

  it("envía el token y devuelve el JSON", async () => {
    const f = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(200, { ok: 1 }));
    expect(await request("/x")).toEqual({ ok: 1 });
    expect((f.mock.calls[0]![1]!.headers as Headers).get("Authorization")).toBe("Bearer old");
  });
  it("renueva el token ante un 401 y reintenta UNA vez", async () => {
    const f = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json(401, { detail: "expirado" }))
      .mockResolvedValueOnce(json(200, { access_token: "new", refresh_token: "r2" }))
      .mockResolvedValueOnce(json(200, { ok: true }));
    expect(await request("/x")).toEqual({ ok: true });
    expect(getSession()).toEqual({ access: "new", refresh: "r2" });
    expect(f).toHaveBeenCalledTimes(3);
  });
  it("si no se puede renovar, cierra la sesión", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(json(401, { detail: "x" })).mockResolvedValueOnce(json(401, {}));
    await expect(request("/x")).rejects.toBeInstanceOf(ApiError);
    expect(getSession()).toBeNull();
  });
  it("traduce el detalle del servidor a un mensaje legible", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json(422, { detail: [{ msg: "email inválido" }, { msg: "contraseña corta" }] }));
    await expect(request("/x", { method: "POST", json: {} })).rejects.toMatchObject({ status: 422, message: "email inválido; contraseña corta" });
  });
  it("las rutas /auth no intentan renovar", async () => {
    const f = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(401, { detail: "credenciales" }));
    await expect(request("/auth/login", { method: "POST", json: {} })).rejects.toMatchObject({ status: 401, message: "credenciales" });
    expect(f).toHaveBeenCalledTimes(1);
  });
});
