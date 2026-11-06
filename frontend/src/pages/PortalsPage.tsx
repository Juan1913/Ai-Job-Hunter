import { useApplyData, useCapabilities } from "@/features/autoapply/useAutoApply";
import { ApplyDataForm } from "@/features/autoapply/ui/ApplyDataForm";
import { usePortals } from "@/features/portals/usePortals";
import { AddPortalForm } from "@/features/portals/ui/AddPortalForm";
import { PortalList } from "@/features/portals/ui/PortalList";
import { ErrorNote, Skeleton } from "@/ui/primitives";

export function PortalsPage() {
  const { list, add, remove } = usePortals();
  const caps = useCapabilities();
  const data = useApplyData();
  return (
    <div className="stack-lg">
      <header className="page-head"><h1>Portales y mis datos</h1><p className="muted">Añade las páginas de empleo que quieras vigilar y deja listos los datos con los que se rellenan los formularios.</p></header>
      <div className="pt-layout">
        <div className="stack-lg">
          <AddPortalForm busy={add.isPending} error={add.error} onAdd={(p) => add.mutate(p)} />
          {list.isLoading ? <Skeleton lines={5} /> : list.data && <PortalList portals={list.data} automatable={caps.data?.automatable ?? []} onDelete={(id) => remove.mutate(id)} />}
          <ErrorNote error={list.error ?? remove.error} />
        </div>
        {data.isLoading ? <Skeleton lines={8} /> : data.data && <ApplyDataForm data={data.data} busy={data.save.isPending} error={data.save.error ?? data.error} onSave={(d) => data.save.mutate(d)} />}
      </div>
    </div>
  );
}
