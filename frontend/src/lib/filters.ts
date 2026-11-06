import type { JobFilters } from "@/api/types";

/** Cuántos filtros hay activos (sin contar el orden), para el contador del botón y el «Limpiar». */
export function activeFilterCount(f: JobFilters): number {
  return [f.q, f.recommendation, f.workMode, f.employmentType, f.minSalary, f.postedDays, f.country].filter((v) => v !== undefined && v !== "").length;
}
