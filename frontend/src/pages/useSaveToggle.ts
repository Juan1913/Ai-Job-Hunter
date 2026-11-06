import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";

/** Guardar / quitar de guardadas desde una tarjeta (sin entrar al detalle). */
export function useSaveToggle() {
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: ({ id, saved }: { id: number; saved: boolean }) => api.setStatus(id, saved ? "saved" : "new"),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["jobs"] }); },
  });
  return (id: number, saved: boolean) => m.mutate({ id, saved });
}
