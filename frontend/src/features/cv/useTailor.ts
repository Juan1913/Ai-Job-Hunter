import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { CvFormat, CvTemplate } from "@/api/types";

export function useCvs(jobId: number) {
  return useQuery({ queryKey: ["cvs", jobId], queryFn: () => api.cvs(jobId), enabled: Number.isFinite(jobId) });
}
/** Genera el CV adaptado a la oferta. `confirmed` = habilidades que el usuario afirma tener (nada más entra al CV). */
export function useTailor(jobId: number) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (confirmed: string[]) => api.tailor(jobId, confirmed), onSuccess: () => { void qc.invalidateQueries({ queryKey: ["cvs", jobId] }); } });
}
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function useDownloadCv() {
  return useMutation({
    mutationFn: async (v: { cvId: number; format: CvFormat; template: CvTemplate }) => {
      saveBlob(await api.cvFile(v.cvId, v.format, v.template), `cv-${v.cvId}-${v.template}.${v.format}`);
    },
  });
}
