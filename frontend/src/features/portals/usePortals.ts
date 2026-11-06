import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";

export function usePortals() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["portals"], queryFn: api.portals });
  const refresh = () => { void qc.invalidateQueries({ queryKey: ["portals"] }); };
  const add = useMutation({ mutationFn: api.addPortal, onSuccess: refresh });
  const remove = useMutation({ mutationFn: api.deletePortal, onSuccess: refresh });
  return { list, add, remove };
}
