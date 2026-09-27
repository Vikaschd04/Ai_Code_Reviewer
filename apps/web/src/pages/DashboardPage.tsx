import { fetchProjectOverview, fetchReadiness, listProjects } from "../api/endpoints";
import type { ProjectOverview } from "../api/client";
import { StatTile } from "../components/Charts";
import { Alert, Empty, Loading } from "../components/Common";
import { Icon } from "../components/Icon";
import { SeverityStackBar, severityCounts } from "../components/Severity";
import { StatusBadge, findingTotal } from "../components/Status";
import { formatRelative, shortHash } from "../lib/format";
import { useAsync } from "../lib/useAsync";

const CARD_LIMIT = 12;

async function loadOverviews(signal: AbortSignal): Promise<ProjectOverview[]> {
  const projects = await listProjects(signal);
  return Promise.all(projects.slice(0, CARD_LIMIT).map((p) => fetchProjectOverview(p.id, signal)));
}

function ProjectCard({ overview }: { overview: ProjectOverview }) {
  const { project, latest_scan: scan, latest_snapshot: snapshot } = overview;
  const counts = severityCounts(scan?.summary?.by_severity);
  return (
    <article className="card stack" aria-labelledby={`p-${project.id}`}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3 id={`p-${project.id}`} className="card-title">
          <a href={`#/projects/${project.id}`}>{project.name}</a>
        </h3>
        {project.origin === "synthetic_fixture" ? (
          <span className="badge badge-neutral">Synthetic fixture</span>
        ) : null}
      </div>
      {scan ? (
        <>
          <div className="row small secondary">
            <StatusBadge state={scan.state} />
            <span>Last scan {formatRelative(scan.finished_at ?? scan.created_at)}</span>
          </div>
          <SeverityStackBar counts={counts} label={`Findings in ${project.name}`} />
        </>
      ) : (
        <p className="muted small" style={{ margin: 0 }}>
          {snapshot
            ? "Snapshot ready — no scan yet."
            : "No source yet. Upload a ZIP or capture a folder."}
        </p>
      )}
      <div className="row small muted" style={{ justifyContent: "space-between" }}>
        <span>
          {overview.snapshot_count} snapshot{overview.snapshot_count === 1 ? "" : "s"} ·{" "}
          {overview.scan_count} scan
          {overview.scan_count === 1 ? "" : "s"}
        </span>
        {snapshot ? <span className="hash">{shortHash(snapshot.manifest_sha256)}</span> : null}
      </div>
    </article>
  );
}

export function DashboardPage() {
  const overviews = useAsync(loadOverviews, []);
  const readiness = useAsync((signal) => fetchReadiness(signal), []);
  const data = overviews.data ?? [];
  const openFindings = data.reduce(
    (sum, o) => sum + (findingTotal(o.latest_scan?.summary) ?? 0),
    0,
  );
  const scans = data.reduce((sum, o) => sum + o.scan_count, 0);
  const snapshots = data.reduce((sum, o) => sum + o.snapshot_count, 0);
  const failing = readiness.data?.checks.filter((c) => c.status !== "ok") ?? [];

  return (
    <>
      {readiness.data && failing.length > 0 ? (
        <Alert tone="warn">
          <p>
            Platform not fully ready: {failing.map((c) => c.name.replaceAll("_", " ")).join(", ")}.{" "}
            <a href="#/operations">Open operations</a>
          </p>
        </Alert>
      ) : null}
      <section className="grid grid-4" aria-label="Summary">
        <StatTile
          label="Projects"
          value={data.length}
          note={data.length ? "Workspace-scoped" : "Create your first project"}
        />
        <StatTile label="Snapshots" value={snapshots} note="Frozen, content-addressed" />
        <StatTile label="Scans" value={scans} note="PMD · ESLint · structure" />
        <StatTile
          label="Findings (latest scans)"
          value={openFindings}
          note="Deterministic rules, exact spans"
        />
      </section>
      <section aria-labelledby="projects-heading" className="stack">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 id="projects-heading" className="card-title">
            Projects
          </h2>
          <a className="btn btn-primary btn-sm" href="#/projects">
            <Icon name="projects" size={15} /> Manage projects
          </a>
        </div>
        {overviews.error ? <Alert tone="bad">{overviews.error}</Alert> : null}
        {overviews.loading && !overviews.data ? <Loading /> : null}
        {overviews.data && data.length === 0 ? (
          <div className="card">
            <Empty title="No projects yet">
              <p>
                Create a project, then upload a ZIP or capture a local folder to run the first scan.
              </p>
              <a className="btn btn-primary" href="#/projects">
                Create a project
              </a>
            </Empty>
          </div>
        ) : null}
        <div className="grid grid-auto">
          {data.map((overview) => (
            <ProjectCard key={overview.project.id} overview={overview} />
          ))}
        </div>
      </section>
    </>
  );
}
