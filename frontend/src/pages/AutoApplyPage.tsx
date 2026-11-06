import { useNavigate } from "react-router-dom";
import { Lightning } from "@phosphor-icons/react";
import { useAttemptActions, useAttempts, useCapabilities, usePolicy } from "@/features/autoapply/useAutoApply";
import { PolicyForm } from "@/features/autoapply/ui/PolicyForm";
import { AttemptList } from "@/features/autoapply/ui/AttemptList";
import { policySentence } from "@/lib/autoapply";
import { ErrorNote, Skeleton } from "@/ui/primitives";

export function AutoApplyPage() {
  const nav = useNavigate();
  const policy = usePolicy();
  const caps = useCapabilities();
  const attempts = useAttempts();
  const act = useAttemptActions();
  const busy = act.approve.isPending || act.reject.isPending || act.retry.isPending;
  return (
    <div className="stack-lg">
      <header className="page-head">
        <h1>Auto-postulación</h1>
        <p className="muted">Opcional y bajo tu control. Por defecto está apagada. Si la activas, tú eliges cuándo, a qué y con qué CV; y puedes pausarla al instante.</p>
        {policy.data && <p className={`aa-status aa-status-${policy.data.mode}`}><Lightning size={16} weight="fill" /> {policySentence(policy.data)}</p>}
      </header>
      {policy.isLoading ? <Skeleton lines={6} /> : policy.data && <PolicyForm policy={policy.data} caps={caps.data} busy={policy.save.isPending} error={policy.save.error} onSave={(p) => policy.save.mutate(p)} />}
      <ErrorNote error={attempts.error ?? act.error} />
      {attempts.data && <AttemptList attempts={attempts.data} busy={busy} onApprove={(id) => act.approve.mutate({ id })} onReject={(id) => act.reject.mutate(id)} onRetry={(id) => act.retry.mutate(id)} onOpenJob={(id) => nav(`/jobs/${id}`)} />}
    </div>
  );
}
