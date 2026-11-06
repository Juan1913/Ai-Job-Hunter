import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

/** ¿Hay IA disponible en este despliegue? (JH_AI_MODE distinto de none). */
export function useAiStatus() {
  const q = useQuery({ queryKey: ["ai-status"], queryFn: api.aiStatus, staleTime: 5 * 60_000 });
  return { available: q.data?.ai_available ?? false, mode: q.data?.mode };
}
