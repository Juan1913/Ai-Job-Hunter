import { Suspense, lazy } from "react";
import { Navigate, Outlet, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "@/features/auth/useAuth";
import { useTheme } from "@/features/theme/useTheme";
import { DashboardPage } from "@/pages/DashboardPage";
import { JobDetailPage } from "@/pages/JobDetailPage";
import { JobsPage } from "@/pages/JobsPage";
import { AutoApplyPage } from "@/pages/AutoApplyPage";
import { SchedulesPage } from "@/pages/SchedulesPage";
import { PortalsPage } from "@/pages/PortalsPage";
import { TrackingPage } from "@/pages/TrackingPage";
import { LoginPage } from "@/pages/LoginPage";
import { ProfilePage } from "@/pages/ProfilePage";
import { Skeleton } from "@/ui/primitives";
import { AppShell, type NavId } from "@/ui/AppShell";

const MapPage = lazy(() => import("@/pages/MapPage").then((m) => ({ default: m.MapPage })));   // Leaflet pesa: solo se descarga al abrir el mapa
const PATH: Record<NavId, string> = { home: "/", jobs: "/jobs", map: "/map", tracking: "/tracking", schedules: "/schedules", portals: "/portals", auto: "/auto", profile: "/profile" };
function Shell() {
  const { authenticated, signOut } = useAuth();
  const { theme, toggle } = useTheme();
  const nav = useNavigate();
  const { pathname } = useLocation();
  if (!authenticated) return <Navigate to="/login" replace />;
  const active: NavId = pathname.startsWith("/jobs") ? "jobs" : pathname.startsWith("/map") ? "map" : pathname.startsWith("/tracking") ? "tracking" : pathname.startsWith("/schedules") ? "schedules" : pathname.startsWith("/portals") ? "portals" : pathname.startsWith("/auto") ? "auto" : pathname.startsWith("/profile") ? "profile" : "home";
  return <AppShell active={active} dark={theme === "dark"} onNavigate={(id) => nav(PATH[id])} onToggleTheme={toggle} onSignOut={() => void signOut()}><Suspense fallback={<Skeleton lines={5} />}><Outlet /></Suspense></AppShell>;
}
export function App() {
  useTheme();
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<Shell />}>
        <Route index element={<DashboardPage />} />
        <Route path="jobs" element={<JobsPage />} />
        <Route path="jobs/:id" element={<JobDetailPage />} />
        <Route path="map" element={<MapPage />} />
        <Route path="tracking" element={<TrackingPage />} />
        <Route path="schedules" element={<SchedulesPage />} />
        <Route path="portals" element={<PortalsPage />} />
        <Route path="auto" element={<AutoApplyPage />} />
        <Route path="profile" element={<ProfilePage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
