import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

/** La separación lógica/visual se comprueba, no se promete: una UI que importe la API o react-query rompe el build. */
describe("arquitectura: la capa visual no toca datos", () => {
  it("rechaza que un componente de ui/ importe el cliente, react-query o un hook de datos", async () => {
    const eslint = new ESLint({ cwd: process.cwd() });
    const code = `import { api } from "@/api/client";\nimport { useQuery } from "@tanstack/react-query";\nimport { useJobs } from "@/features/jobs/useJobs";\nexport const X = () => null; void [api, useQuery, useJobs];`;
    const [r] = await eslint.lintText(code, { filePath: "src/features/jobs/ui/Bad.tsx" });
    expect(r!.messages.filter((m) => m.ruleId?.includes("no-restricted-imports")).length).toBe(3);
  });
  it("permite importar solo TIPOS del contrato", async () => {
    const eslint = new ESLint({ cwd: process.cwd() });
    const [r] = await eslint.lintText(`import type { Profile } from "@/api/types";\nexport const x = (p: Profile) => p.full_name;`, { filePath: "src/features/profile/ui/Ok.tsx" });
    expect(r!.messages.filter((m) => m.ruleId?.includes("no-restricted-imports"))).toEqual([]);
  });
});
