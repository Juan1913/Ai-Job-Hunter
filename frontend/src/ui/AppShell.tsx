import { useState, type ReactNode } from "react";
import { Briefcase, House, ClockClockwise, DotsThreeOutline, GlobeHemisphereWest, Lightning, MapTrifold, Moon, PaperPlaneTilt, SignOut, Sun, UserCircle } from "@phosphor-icons/react";

export type NavId = "home" | "jobs" | "map" | "tracking" | "schedules" | "portals" | "auto" | "profile";
const ITEMS: { id: NavId; label: string; icon: ReactNode }[] = [
  { id: "home", label: "Panel", icon: <House size={20} /> },
  { id: "jobs", label: "Ofertas", icon: <Briefcase size={20} /> },
  { id: "map", label: "Mapa", icon: <GlobeHemisphereWest size={20} /> },
  { id: "tracking", label: "Seguimiento", icon: <PaperPlaneTilt size={20} /> },
  { id: "schedules", label: "Programadas", icon: <ClockClockwise size={20} /> },
  { id: "portals", label: "Portales", icon: <MapTrifold size={20} /> },
  { id: "auto", label: "Auto-postulación", icon: <Lightning size={20} /> },
  { id: "profile", label: "Mi perfil", icon: <UserCircle size={20} /> },
];
const SECONDARY: NavId[] = ["schedules", "portals", "auto", "profile"];   // en móvil van bajo «Más»
export interface AppShellProps { active: NavId; dark: boolean; onNavigate: (id: NavId) => void; onToggleTheme: () => void; onSignOut: () => void; children: ReactNode }

/** Marco de la app: barra lateral en escritorio, barra inferior en móvil. */
export function AppShell({ active, dark, onNavigate, onToggleTheme, onSignOut, children }: AppShellProps) {
  const today = new Date().toLocaleDateString("es-CO", { weekday: "long", day: "numeric", month: "long" });
  const [more, setMore] = useState(false);
  const go = (id: NavId) => { setMore(false); onNavigate(id); };
  const secondaryActive = SECONDARY.includes(active);
  const current = ITEMS.find((i) => i.id === active)?.label ?? "";
  return (
    <div className="shell">
      <aside className="side">
        <div className="brand"><span className="brand-mark" aria-hidden />JobHunter</div>
        <nav aria-label="Principal">
          {ITEMS.map((i) => (
            <button key={i.id} className={`nav-item ${SECONDARY.includes(i.id) ? "nav-extra" : ""} ${active === i.id ? "is-active" : ""}`} aria-current={active === i.id ? "page" : undefined} onClick={() => go(i.id)}>{i.icon}<span>{i.label}</span></button>
          ))}
          <button className={`nav-item nav-more ${secondaryActive || more ? "is-active" : ""}`} aria-expanded={more} aria-label="Más opciones" onClick={() => setMore((v) => !v)}><DotsThreeOutline size={20} weight="fill" /><span>Más</span></button>
        </nav>
        {more && (
          <div className="more-sheet" role="menu">
            {ITEMS.filter((i) => SECONDARY.includes(i.id)).map((i) => <button key={i.id} role="menuitem" className={`nav-item ${active === i.id ? "is-active" : ""}`} onClick={() => go(i.id)}>{i.icon}<span>{i.label}</span></button>)}
            <button role="menuitem" className="nav-item" onClick={onToggleTheme}>{dark ? <Sun size={20} /> : <Moon size={20} />}<span>{dark ? "Modo claro" : "Modo oscuro"}</span></button>
            <button role="menuitem" className="nav-item" onClick={onSignOut}><SignOut size={20} /><span>Salir</span></button>
          </div>
        )}
        <div className="side-foot">
          <button className="nav-item" onClick={onToggleTheme} aria-label={dark ? "Cambiar a modo claro" : "Cambiar a modo oscuro"}>{dark ? <Sun size={20} /> : <Moon size={20} />}<span>{dark ? "Modo claro" : "Modo oscuro"}</span></button>
          <button className="nav-item" onClick={onSignOut}><SignOut size={20} /><span>Salir</span></button>
        </div>
      </aside>
      <div className="main-col">
        <div className="topbar"><span className="crumb"><i aria-hidden />{current}</span><span className="topbar-date">{today}</span></div>
        <main className="main">{children}</main>
      </div>
    </div>
  );
}
