import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { ApiError } from "@/api/http";

export const profileKey = ["profile"] as const;

/** Perfil del usuario. 409 (o 404) = aún no subió su CV: no es un error de pantalla, es el inicio del onboarding. */
export function useProfile() {
  return useQuery({
    queryKey: profileKey,
    queryFn: async () => { try { return await api.profile(); } catch (e) { if (e instanceof ApiError && (e.status === 409 || e.status === 404)) return null; throw e; } },
  });
}
export function useUploadCv() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (f: File) => api.uploadCv(f), onSuccess: (p) => { qc.setQueryData(profileKey, p); } });
}
