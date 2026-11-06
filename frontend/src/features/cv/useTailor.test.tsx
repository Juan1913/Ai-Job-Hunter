import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { api } from "@/api/client";
import { useTailor } from "./useTailor";

const wrap = ({ children }: { children: ReactNode }) => <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;

describe("useTailor", () => {
  it("manda al servidor SOLO lo que el usuario confirmó", async () => {
    const spy = vi.spyOn(api, "tailor").mockResolvedValue({ id: 1 } as never);
    const { result } = renderHook(() => useTailor(7), { wrapper: wrap });
    await act(async () => { result.current.mutate(["AWS"]); });
    await waitFor(() => expect(spy).toHaveBeenCalledWith(7, ["AWS"]));
  });
});
