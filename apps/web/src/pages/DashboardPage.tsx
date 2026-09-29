import type { Principal, ProjectOverview } from "../api/client";
import { fetchProjectOverview, fetchReadiness, listProjects } from "../api/endpoints";
import { StatTile } from "../components/Charts";
import { Alert, Loading, PageHeader } from "../components/Common";
import { Icon } from "../components/Icon";
import { SampleCard } from "../components/SampleCard";
import { SeverityStackBar, severityCounts } from "../components/Severity";
import { StatusBadge, findingTotal } from "../components/Status";
import { formatRelative } from "../lib/format";
import { plural } from "../lib/labels";
import { writableWorkspace } from "../lib/session";
import { useAsync } from "../lib/useAsync";

const CARD_LIMIT = 12;

async function loadOverviews(signal: AbortSignal): Promise<ProjectOverview[]> {
  const projects = await listProjects(signal);
  const newest = [...projects].reverse().slice(0, CARD_LIMIT);
  return Promise.all(newest.map((p) => fetchProjectOverview(p.id, signal)));
}

function ProjectCard({ overview }: { overview: ProjectOverview }) {
  const { project, latest_scan: scan, latest_snapshot: snapshot } = overview;
  const counts = severityCounts(scan?.summary?.by_severity);
  const total = findingTotal(scan?.summary);
  return (
    <article className="card stack project-card" aria-labelledby={`p-${project.id}`}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "nowrap" }}>
        <h3 id={`p-${project.id}`} className="card-title truncate">
          <a href={`#/projects/${project.id}`}>{project.name}</a>
        </h3>
        {project.origin === "synthetic_fixture" ? (
          <span className="badge badge-neutral">Sample</span>
        ) : null}
      </div>
      {scan ? (
        <>
          <div className="row small secondary">
            <StatusBadge state={scan.state} />
            <span>Reviewed {formatRelative(scan.finished_at ?? scan.created_at)}</span>
          </div>
          <div className="project-card-total">
            <span className="tile-value">{total ?? "—"}</span>
            <span className="muted small">findings in the latest review</span>
          </div>
          <SeverityStackBar counts={counts} label={`Findings in ${project.name}`} />
        </>
      ) : (
        <p className="muted small" style={{ margin: 0 }}>
          {snapshot ? "Code uploaded — ready for its first review." : "No code uploaded yet."}
        </p>
      )}
      <a className="btn btn-ghost btn-sm card-link" href={`#/projects/${project.id}`}>
        Open project <Icon name="arrow" size={14} />
      </a>
    </article>
  );
}

function ReadinessNotice() {
  const readiness = useAsync((signal) => fetchReadiness(signal), []);
  const failing = readiness.data?.checks.filter((c) => c.status !== "ok") ?? [];
  if (!readiness.data || failing.length === 0) return null;
  return (
    <Alert tone="warn">
      <p>
        Some parts of refactorX are not running, so reviews may not start.{" "}
        <a href="#/operations">See system status</a>
      </p>
    </Alert>
  );
}

export function DashboardPage({ principal }: { principal: Principal }) {
  const overviews = useAsync(loadOverviews, []);
  const data = overviews.data ?? [];
  const workspace = writableWorkspace(principal);
  const reviewed = data.filter((o) => o.latest_scan);
  const findings = reviewed.reduce(
    (sum, o) => sum + (findingTotal(o.latest_scan?.summary) ?? 0),
    0,
  );
  const urgent = reviewed.reduce((sum, o) => {
    const counts = severityCounts(o.latest_scan?.summary?.by_severity);
    return sum + counts.critical + counts.high;
  }, 0);
  const reviews = data.reduce((sum, o) => sum + o.scan_count, 0);
  const hasSample = data.some((o) => o.project.origin === "synthetic_fixture");

  return (
    <>
      <PageHeader
        title={
          principal.is_demo ? (
            <>
              Welcome to the <span className="gradient-text">refactorX</span> demo
            </>
          ) : (
            <span className="gradient-text">Overview</span>
          )
        }
        sub="Your projects and what their latest reviews found."
        actions={
          <a className="btn btn-primary" href="#/projects">
            <Icon name="upload" size={16} /> Review new code
          </a>
        }
      />
      {principal.is_operator ? <ReadinessNotice /> : null}
      {overviews.error ? <Alert tone="bad">{overviews.error}</Alert> : null}
      {overviews.loading && !overviews.data ? <Loading /> : null}

      {overviews.data && data.length === 0 ? (
        <div className="grid grid-2">
          {workspace ? <SampleCard workspaceId={workspace} /> : null}
          <section className="card stack" aria-labelledby="own-code-title">
            <div className="row" style={{ alignItems: "flex-start", flexWrap: "nowrap" }}>
              <span className="feature-icon" aria-hidden="true">
                <Icon name="upload" size={22} />
              </span>
              <div className="stack" style={{ gap: 6 }}>
                <h2 id="own-code-title" className="card-title">
                  Review your own code
                </h2>
                <p className="secondary small" style={{ margin: 0 }}>
                  Create a project, upload a ZIP of your source code and start a review. Java,
                  JavaScript and TypeScript are checked in depth; dependencies and secrets in any
                  project.
                </p>
              </div>
            </div>
            <div>
              <a className="btn btn-ghost" href="#/projects">
                <Icon name="projects" size={15} /> Create a project
              </a>
            </div>
          </section>
        </div>
      ) : null}

      {data.length > 0 ? (
        <>
          <section className="grid grid-4" aria-label="Summary">
            <StatTile label="Projects" value={data.length} note="In your workspace" />
            <StatTile label="Findings" value={findings} note="In the latest reviews" />
            <StatTile label="Critical & high" value={urgent} note="Fix these first" />
            <StatTile label="Reviews run" value={reviews} note="Across all projects" />
          </section>
          <section aria-labelledby="projects-heading" className="stack">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h2 id="projects-heading" className="card-title">
                Recent projects
              </h2>
              <a className="btn btn-ghost btn-sm" href="#/projects">
                All projects ({plural(data.length, "project")}) <Icon name="arrow" size={14} />
              </a>
            </div>
            <div className="grid grid-auto">
              {data.map((overview) => (
                <ProjectCard key={overview.project.id} overview={overview} />
              ))}
            </div>
          </section>
          {!hasSample && workspace ? <SampleCard workspaceId={workspace} compact /> : null}
        </>
      ) : null}
    </>
  );
}
