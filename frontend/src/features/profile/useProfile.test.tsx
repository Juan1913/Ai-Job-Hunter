import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { api } from "@/api/client";
import { ApiError } from "@/api/http";
import { useProfile } from "./useProfile";

const wrap = ({ children }: { children: ReactNode }) => <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{children}</QueryClientProvider>;

describe("useProfile", () => {
  it("sin CV subido (409) el perfil es null, no un error", async () => {
    vi.spyOn(api, "profile").mockRejectedValue(new ApiError(409, "Sube tu CV primero"));
    const { result } = renderHook(() => useProfile(), { wrapper: wrap });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
  });
  it("otros errores sí se propagan", async () => {
    vi.spyOn(api, "profile").mockRejectedValue(new ApiError(500, "boom"));
    const { result } = renderHook(() => useProfile(), { wrapper: wrap });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
