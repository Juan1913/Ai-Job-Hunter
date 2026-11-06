import type { Profile } from "@/api/types";
import { Badge, Card } from "@/ui/primitives";

export function ProfileSummary({ profile }: { profile: Profile }) {
  return (
    <Card title={profile.full_name || "Tu perfil"}>
      <p className="muted">{[profile.headline, profile.location, profile.email].filter(Boolean).join(" · ")}</p>
      {profile.summary && <p>{profile.summary}</p>}
      <h3>Habilidades detectadas ({profile.skills.length})</h3>
      <div className="chips">{profile.skills.map((s) => <Badge key={s.name}>{s.name}</Badge>)}</div>
      <h3>Experiencia ({profile.experience.length})</h3>
      <ul className="exp-list">
        {profile.experience.map((e, i) => (
          <li key={i}>
            <strong>{e.title || "—"}</strong>{e.company ? ` · ${e.company}` : <Badge tone="warn" title="No detectamos la empresa en tu CV">sin empresa</Badge>}
            <span className="muted"> {[e.start, e.end ?? (e.start ? "actualidad" : null)].filter(Boolean).join(" – ")} · {e.bullets.length} logros</span>
          </li>
        ))}
      </ul>
      {profile.education.length > 0 && <>
        <h3>Educación</h3>
        <ul className="plain">{profile.education.map((e, i) => <li key={i}>{[e.degree, e.institution, e.year].filter(Boolean).join(" · ")}</li>)}</ul>
      </>}
    </Card>
  );
}
