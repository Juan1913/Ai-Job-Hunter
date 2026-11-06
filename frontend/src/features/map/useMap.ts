import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

export function useJobsMap() {
  return useQuery({ queryKey: ["jobs-map"], queryFn: api.mapJobs, staleTime: 30_000 });
}
