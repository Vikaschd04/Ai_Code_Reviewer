import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  describeError,
  type AuthOptions,
  type Capability,
  type Principal,
} from "./api/client";
import { fetchAuthOptions, fetchCapabilities, fetchPrincipal, signOut } from "./api/endpoints";
import { Alert, PageHeader } from "./components/Common";
import { BrandMark, Icon, Wordmark, type IconName } from "./components/Icon";
import { useRoute, type Route } from "./lib/router";
import { SessionContext } from "./lib/session";
import { activeTheme, applyTheme, type Theme } from "./lib/theme";
import { AiRunPage } from "./pages/AiRunPage";
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
  { id: "dashboard", label: "Overview", href: "#/", icon: "dashboard" },
  { id: "projects", label: "Projects", href: "#/projects", icon: "projects" },
];

const ROUTE_LABELS: Record<Route["name"], string> = {
  dashboard: "Overview",
  projects: "Projects",
  project: "Project",
  snapshot: "Upload",
  scan: "Review",
  finding: "Finding",
  "ai-run": "AI answer",
  operations: "System status",
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

function NavLink({
  href,
  icon,
  label,
  current,
}: {
  href: string;
  icon: IconName;
  label: string;
  current: boolean;
}) {
  return (
    <a className="nav-link" href={href} aria-current={current ? "page" : undefined}>
      <Icon name={icon} />
      {label}
    </a>
  );
}

function Sidebar({
  capabilities,
  route,
  principal,
}: {
  capabilities: Capability[];
  route: Route;
  principal: Principal | null;
}) {
  const current = sectionOf(route);
  const planned = capabilities.filter((c) => c.state === "planned");
  const aiAvailable = capabilities.some(
    (c) => c.id === "ai_investigation" && c.state === "available",
  );
  const operator = principal?.is_operator === true;
  return (
    <aside className="sidebar">
      <a className="brand" href="#/" aria-label="refactorX overview">
        <BrandMark />
        <Wordmark className="brand-name" />
      </a>
      <nav aria-label="Primary">
        <ul className="nav">
          {LINKS.map((link) => (
            <li key={link.id}>
              {principal ? (
                <NavLink
                  href={link.href}
                  icon={link.icon}
                  label={link.label}
                  current={current === link.id}
                />
              ) : (
                <span className="nav-planned">
                  <Icon name={link.icon} />
                  {link.label}
                  <small>Unavailable right now</small>
                </span>
              )}
            </li>
          ))}
        </ul>
        {operator || !principal ? (
          <div className="nav-group">
            <div className="nav-group-label">Administration</div>
            <ul className="nav">
              <li>
                <NavLink
                  href="#/operations"
                  icon="pulse"
                  label="System status"
                  current={current === "operations"}
                />
              </li>
            </ul>
          </div>
        ) : null}
      </nav>
      {planned.length > 0 ? (
        <div className="sidebar-planned">
          <div className="nav-group-label">Coming soon</div>
          <ul className="nav" aria-label="Coming soon">
            {planned.map((capability) => (
              <li key={capability.id}>
                <span className="nav-planned" aria-disabled="true" title={capability.reason}>
                  <Icon name={PLANNED_ICONS[capability.id] ?? "info"} />
                  {capability.label}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="sidebar-footer">
        <Icon name="lock" size={12} />{" "}
        {aiAvailable
          ? "Code leaves this server only for AI review, in projects where an admin switched it on."
          : "Your code stays on this server. Nothing is sent to AI services."}
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

function DemoBanner() {
  return (
    <div className="demo-banner" role="note" data-testid="demo-banner">
      <Icon name="users" size={16} />
      <p>
        <strong>You are in the shared demo workspace.</strong> Other visitors can see what you
        upload here, so only use sample or public code.
      </p>
    </div>
  );
}

function Page({ route, principal }: { route: Route; principal: Principal | null }) {
  if (!principal) {
    return (
      <>
        <PageHeader title="System status" sub="Whether every part of refactorX is running." />
        <OperationsPage canOperate={false} />
      </>
    );
  }
  switch (route.name) {
    case "dashboard":
      return <DashboardPage principal={principal} />;
    case "projects":
      return (
        <>
          <PageHeader
            title="Projects"
            sub="A project holds one codebase: its uploads, reviews and tracked issues."
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
    case "ai-run":
      return <AiRunPage key={route.id} runId={route.id} />;
    case "operations":
      return principal.is_operator ? (
        <>
          <PageHeader title="System status" sub="Whether every part of refactorX is running." />
          <OperationsPage canOperate />
        </>
      ) : (
        <>
          <PageHeader title="System status" />
          <Alert tone="info">Only administrators can see the system status.</Alert>
        </>
      );
    case "not-found":
      return (
        <>
          <PageHeader title="Page not found" sub="This page does not exist." />
          <a className="btn btn-primary" href="#/">
            Go to the overview
          </a>
        </>
      );
  }
}

export function App() {
  const [session, setSession] = useState<Session>({ kind: "checking" });
  const [options, setOptions] = useState<AuthOptions | null>(null);
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
    fetchAuthOptions().then(
      (next) => {
        if (active) setOptions(next);
      },
      () => {
        if (active) setOptions(null);
      },
    );
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
        <div className="stack" aria-busy="true">
          <BrandMark />
          <p className="muted">Loading refactorX…</p>
        </div>
      </div>
    );
  }
  if (session.kind === "unreachable") {
    return (
      <main className="centered">
        <div className="card unreachable-card">
          <h1>
            <Wordmark />
          </h1>
          <Alert tone="bad">
            refactorX is not reachable right now. If the service was asleep, it may need up to a
            minute to start. ({session.reason})
          </Alert>
          <button type="button" className="btn btn-primary" onClick={loadSession}>
            Try again
          </button>
        </div>
      </main>
    );
  }
  if (session.kind === "anonymous") {
    return <LoginPage options={options} onSignedIn={loadSession} />;
  }

  const principal = session.kind === "signed-in" ? session.principal : null;
  const initials = (principal?.display_name ?? "?")
    .split(" ")
    .map((part) => part.charAt(0))
    .join("")
    .slice(0, 2)
    .toUpperCase();

  return (
    <SessionContext.Provider value={{ principal, options }}>
      <div className="shell">
        <a className="skip-link" href="#main">
          Skip to content
        </a>
        <Sidebar capabilities={capabilities} route={route} principal={principal} />
        <div className="main">
          <header className="topbar">
            <Crumbs route={route} />
            <div className="topbar-actions">
              {principal?.is_demo ? (
                <span className="badge badge-live">Demo</span>
              ) : options?.environment === "local" ? (
                <span className="badge badge-neutral">Local</span>
              ) : null}
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
                  <span className="user-name">{principal.display_name}</span>
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
            {principal?.is_demo ? <DemoBanner /> : null}
            {session.kind === "degraded" ? (
              <Alert tone="bad">
                You are signed in, but your account could not be loaded: {session.reason}
              </Alert>
            ) : null}
            <Page route={route} principal={principal} />
          </main>
        </div>
      </div>
    </SessionContext.Provider>
  );
}
