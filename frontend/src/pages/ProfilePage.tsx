import { Link } from "react-router-dom";
import { useProfile, useUploadCv } from "@/features/profile/useProfile";
import { ProfileSummary } from "@/features/profile/ui/ProfileSummary";
import { UploadCv } from "@/features/profile/ui/UploadCv";
import { Card, ErrorNote, Skeleton } from "@/ui/primitives";

export function ProfilePage() {
  const profile = useProfile();
  const upload = useUploadCv();
  if (profile.isLoading) return <Skeleton lines={5} />;
  return (
    <div className="stack-lg">
      <header className="page-head"><h1>Mi perfil</h1><p className="muted">Lo que leímos de tu CV. Si algo no es correcto, sube una versión corregida.</p></header>
      <ErrorNote error={profile.error} />
      <div className={profile.data ? "profile-grid" : ""}>
        {profile.data && <ProfileSummary profile={profile.data} />}
        <div className="stack">
          <Card title={profile.data ? "Actualizar CV" : "Empecemos"}>
            <UploadCv busy={upload.isPending} error={upload.error} onFile={(f) => upload.mutate(f)} />
          </Card>
          {profile.data && <Link className="btn btn-primary" to="/">Ir al panel y buscar ofertas</Link>}
        </div>
      </div>
    </div>
  );
}
