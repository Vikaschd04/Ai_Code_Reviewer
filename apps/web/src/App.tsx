import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, describeError, type Capability, type Principal } from "./api/client";
import { fetchCapabilities, fetchPrincipal, signOut } from "./api/endpoints";
import { Alert, PageHeader } from "./components/Common";
import { BrandMark, Icon, type IconName } from "./components/Icon";
import { useRoute, type Route } from "./lib/router";
import { activeTheme, applyTheme, type Theme } from "./lib/theme";
import { DashboardPage } from "./pages/DashboardPage";
import { FindingPage } from "./pages/FindingPage";
import { LoginPage } from "./pages/LoginPage";
import { OperationsPage } from "./pages/OperationsPage";
import { ProjectPage } from "./pages/ProjectPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { ScanPage } from "./pages/ScanPage";
import { SnapshotPage } from "./pages/SnapshotPage";

type Session =
  | { kind: "checking" }
  | { kind: "anonymous" }
  | { kind: "signed-in"; principal: Principal }
  | { kind: "degraded"; reason: string }
  | { kind: "unreachable"; reason: string };

type Section = "dashboard" | "projects" | "operations";

const PLANNED_ICONS: Record<string, IconName> = {
  architecture_graph: "graph",
  ai_investigation: "sparkles",
  fix_workbench: "wrench",
  git_integration: "branch",
};

const LINKS: { id: Section; label: string; href: string; icon: IconName }[] = [
  { id: "dashboard", label: "Dashboard", href: "#/", icon: "dashboard" },
  { id: "projects", label: "Projects", href: "#/projects", icon: "projects" },
  { id: "operations", label: "Operations", href: "#/operations", icon: "pulse" },
];

const ROUTE_LABELS: Record<Route["name"], string> = {
  dashboard: "Dashboard",
  projects: "Projects",
  project: "Project",
  snapshot: "Snapshot",
  scan: "Scan",
  finding: "Finding",
  operations: "Operations",
  "not-found": "Not found",
};

async function resolveSession(): Promise<Session> {
  try {
    return { kind: "signed-in", principal: await fetchPrincipal() };
  } catch (caught) {
    if (caught instanceof ApiError && caught.status === 401) return { kind: "anonymous" };
    if (caught instanceof ApiError) return { kind: "degraded", reason: describeError(caught) };
    return { kind: "unreachable", reason: describeError(caught) };
  }
}

function sectionOf(route: Route): Section | null {
  if (route.name === "dashboard" || route.name === "operations") return route.name;
  if (route.name === "not-found") return null;
  return "projects";
}

function Sidebar({
  capabilities,
  route,
  degraded,
}: {
  capabilities: Capability[];
  route: Route;
  degraded: boolean;
}) {
  const current = sectionOf(route);
  const planned = capabilities.filter((c) => c.state === "planned");
  return (
    <aside className="sidebar">
      <a className="brand" href="#/">
        <BrandMark />
        <span>
          <span className="brand-name">Code Review</span>
          <span className="brand-sub">Platform</span>
        </span>
      </a>
      <nav aria-label="Primary">
        <ul className="nav">
          {LINKS.map((link) => (
            <li key={link.id}>
              {degraded && link.id !== "operations" ? (
                <span className="nav-planned">
                  <Icon name={link.icon} />
                  {link.label}
                  <small>Unavailable while the database is down</small>
                </span>
              ) : (
                <a
                  className="nav-link"
                  href={link.href}
                  aria-current={current === link.id ? "page" : undefined}
                >
                  <Icon name={link.icon} />
                  {link.label}
                </a>
              )}
            </li>
          ))}
        </ul>
      </nav>
      {planned.length > 0 ? (
        <div>
          <div className="nav-group-label">Coming next</div>
          <ul className="nav" aria-label="Planned capabilities">
            {planned.map((capability) => (
              <li key={capability.id}>
                <span className="nav-planned" aria-disabled="true">
                  <Icon name={PLANNED_ICONS[capability.id] ?? "info"} />
                  {capability.label}
                  <small>
                    {capability.phase}: {capability.reason}
                  </small>
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="sidebar-footer">
        <Icon name="shield" size={12} /> Loopback-only local deployment. No source is sent to AI
        providers.
      </div>
    </aside>
  );
}

function Crumbs({ route }: { route: Route }) {
  return (
    <nav className="crumbs" aria-label="Breadcrumb">
      <a href="#/">Home</a>
      {route.name !== "dashboard" ? (
        <>
          <span aria-hidden="true">/</span>
          <span aria-current="page">{ROUTE_LABELS[route.name]}</span>
        </>
      ) : null}
    </nav>
  );
}

function Page({ route, principal }: { route: Route; principal: Principal | null }) {
  if (!principal) {
    return (
      <>
        <PageHeader title="Operations" sub="Readiness of the platform's dependencies." />
        <OperationsPage canOperate={false} />
      </>
    );
  }
  switch (route.name) {
    case "dashboard":
      return (
        <>
          <PageHeader
            title={<span className="gradient-text">Command center</span>}
            sub="Evidence-backed review of frozen source snapshots. Every number here comes from real scans; nothing is simulated."
          />
          <DashboardPage />
        </>
      );
    case "projects":
      return (
        <>
          <PageHeader
            title="Projects"
            sub="Each project holds sources, frozen snapshots and scans."
          />
          <ProjectsPage principal={principal} />
        </>
      );
    case "project":
      return <ProjectPage key={route.id} projectId={route.id} tab={route.tab} />;
    case "snapshot":
      return <SnapshotPage key={route.id} snapshotId={route.id} tab={route.tab} />;
    case "scan":
      return <ScanPage key={route.id} scanId={route.id} tab={route.tab} />;
    case "finding":
      return <FindingPage key={route.id} findingId={route.id} />;
    case "operations":
      return (
        <>
          <PageHeader
            title="Operations"
            sub="Live readiness of PostgreSQL, Temporal, the worker and artifact storage."
          />
          <OperationsPage canOperate={principal.is_operator} />
        </>
      );
    case "not-found":
      return (
        <>
          <PageHeader title="Not found" sub="This page does not exist." />
          <a className="btn btn-primary" href="#/">
            Go to the dashboard
          </a>
        </>
      );
  }
}

export function App() {
  const [session, setSession] = useState<Session>({ kind: "checking" });
  const [capabilities, setCapabilities] = useState<Capability[]>([]);
  const [theme, setTheme] = useState<Theme>(activeTheme);
  const route = useRoute();
  const mainRef = useRef<HTMLElement>(null);

  const loadSession = useCallback(() => {
    void resolveSession().then(setSession);
  }, []);

  useEffect(() => {
    let active = true;
    void resolveSession().then((next) => {
      if (active) setSession(next);
    });
    return () => {
      active = false;
    };
  }, []);

  const authenticated = session.kind === "signed-in" || session.kind === "degraded";
  useEffect(() => {
    if (!authenticated) return;
    const controller = new AbortController();
    fetchCapabilities(controller.signal).then(setCapabilities, () => {
      setCapabilities([]);
    });
    return () => {
      controller.abort();
    };
  }, [authenticated]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      mainRef.current?.querySelector<HTMLElement>("h1")?.focus();
    }, 50);
    return () => {
      window.clearTimeout(timer);
    };
  }, [route]);

  if (session.kind === "checking") {
    return (
      <div className="centered">
        <div className="stack" style={{ justifyItems: "center" }}>
          <BrandMark />
          <p className="muted">Connecting to the API…</p>
        </div>
      </div>
    );
  }
  if (session.kind === "unreachable") {
    return (
      <main className="centered">
        <div className="card stack" style={{ maxWidth: 460 }}>
          <h1>Code Review Platform</h1>
          <Alert tone="bad">{session.reason}</Alert>
          <button type="button" className="btn btn-primary" onClick={loadSession}>
            Retry
          </button>
        </div>
      </main>
    );
  }
  if (session.kind === "anonymous") {
    return <LoginPage onSignedIn={loadSession} />;
  }

  const principal = session.kind === "signed-in" ? session.principal : null;
  const initials = (principal?.display_name ?? "?")
    .split(" ")
    .map((part) => part.charAt(0))
    .join("")
    .slice(0, 2)
    .toUpperCase();

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <Sidebar capabilities={capabilities} route={route} degraded={!principal} />
      <div className="main">
        <header className="topbar">
          <Crumbs route={route} />
          <div className="topbar-actions">
            <span className="badge badge-live">Local · loopback only</span>
            <button
              type="button"
              className="icon-btn"
              aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              onClick={() => {
                const next = theme === "dark" ? "light" : "dark";
                applyTheme(next);
                setTheme(next);
              }}
            >
              <Icon name={theme === "dark" ? "sun" : "moon"} />
            </button>
            {principal ? (
              <span className="user-chip">
                <span className="avatar" aria-hidden="true">
                  {initials}
                </span>
                {principal.display_name}
              </span>
            ) : null}
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => {
                void signOut().finally(() => {
                  setSession({ kind: "anonymous" });
                });
              }}
            >
              <Icon name="logout" size={15} /> Sign out
            </button>
          </div>
        </header>
        <main id="main" className="content" ref={mainRef}>
          {session.kind === "degraded" ? (
            <Alert tone="bad">
              Signed in, but your platform identity could not be loaded: {session.reason}
            </Alert>
          ) : null}
          <Page route={route} principal={principal} />
        </main>
      </div>
    </div>
  );
}
