import { useRef, useState, type DragEvent } from "react";

import { describeError, type Intake, type Scan, type Snapshot } from "../api/client";
import {
  createIntake,
  fetchIntake,
  fetchIntakePolicy,
  fetchProjectOverview,
  finalizeIntake,
  listScans,
  listSnapshots,
  startScan,
  uploadArchive,
} from "../api/endpoints";
import {
  Alert,
  CopyBlock,
  Disclosure,
  Empty,
  Loading,
  PageHeader,
  Tabs,
} from "../components/Common";
import { DeleteProjectDialog } from "../components/DeleteProjectDialog";
import { Icon } from "../components/Icon";
import { SeverityBars, severityCounts } from "../components/Severity";
import { StatusBadge, findingTotal } from "../components/Status";
import { formatBytes, formatDate, formatRelative } from "../lib/format";
import { plural } from "../lib/labels";
import { reviewLabel, reviewNumbers } from "../lib/reviews";
import { navigate } from "../lib/router";
import { isLocalDevelopment, useSession } from "../lib/session";
import { useAsync } from "../lib/useAsync";
import { AiSettings } from "./AiView";
import { FixesView } from "./FixesView";
import { GitHubView } from "./GitHubView";
import { HealthSummary, InsightsView } from "./InsightsView";
import { IssuesView } from "./IssuesView";
import { WorkspacesView } from "./WorkspacesView";

type UploadPhase = "idle" | "uploading" | "validating" | "done";

/** Old tab names still open the right tab (links from earlier versions). */
const TAB_ALIASES: Record<string, string> = {
  source: "uploads",
  snapshots: "uploads",
  scans: "uploads",
  upload: "uploads",
  reviews: "uploads",
  workspaces: "fixes",
  github: "settings",
  ai: "insights",
  architecture: "insights",
  nfr: "insights",
};
/** Old tabs that are now views of Insights. */
const VIEW_ALIASES: Record<string, string> = { architecture: "architecture", nfr: "nfr" };

async function waitForIntake(id: string): Promise<Intake> {
  for (let attempt = 0; attempt < 600; attempt++) {
    const intake = await fetchIntake(id);
    if (["READY", "REJECTED", "FAILED", "CANCELED"].includes(intake.state)) return intake;
    await new Promise((resolve) => setTimeout(resolve, 700));
  }
  throw new Error("Checking the upload is taking longer than expected; look again in a minute.");
}

function StartReviewButton({
  projectId,
  snapshotId,
  label = "Start review",
  primary = true,
}: {
  projectId: string;
  snapshotId: string;
  label?: string;
  primary?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <>
      <button
        type="button"
        className={primary ? "btn btn-primary" : "btn btn-ghost btn-sm"}
        disabled={busy}
        onClick={() => {
          setBusy(true);
          setError(null);
          startScan(projectId, snapshotId).then(
            (scan) => {
              navigate(`#/scans/${scan.id}`);
            },
            (caught: unknown) => {
              setError(describeError(caught));
              setBusy(false);
            },
          );
        }}
      >
        <Icon name="scan" size={primary ? 16 : 14} /> {busy ? "Starting…" : label}
      </button>
      {error ? <Alert tone="bad">{error}</Alert> : null}
    </>
  );
}

function ZipUpload({ projectId }: { projectId: string }) {
  const policy = useAsync((signal) => fetchIntakePolicy(signal), []);
  const input = useRef<HTMLInputElement>(null);
  const [phase, setPhase] = useState<UploadPhase>("idle");
  const [progress, setProgress] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [result, setResult] = useState<Intake | null>(null);
  const [error, setError] = useState<string | null>(null);
  const limit = policy.data?.limits.max_upload_bytes ?? null;

  async function upload(file: File) {
    setError(null);
    setResult(null);
    if (!file.name.toLowerCase().endsWith(".zip")) {
      setError("Choose a .zip file.");
      return;
    }
    if (limit !== null && file.size > limit) {
      setError(`The file is ${formatBytes(file.size)}; the limit is ${formatBytes(limit)}.`);
      return;
    }
    try {
      setPhase("uploading");
      setProgress(0);
      const intake = await createIntake(projectId, file.name);
      await uploadArchive(intake.id, file, setProgress);
      setPhase("validating");
      await finalizeIntake(intake.id);
      const finished = await waitForIntake(intake.id);
      setResult(finished);
      setPhase("done");
    } catch (caught) {
      setError(describeError(caught));
      setPhase("idle");
    }
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files[0];
    if (file) void upload(file);
  }

  const busy = phase === "uploading" || phase === "validating";
  const violations = (result?.error_details as { violations?: unknown } | null)?.violations;
  return (
    <section className="card stack" aria-labelledby="zip-title">
      <div>
        <h2 id="zip-title" className="card-title">
          <Icon name="upload" size={16} /> Upload your code
        </h2>
        <p className="card-sub">
          Upload a .zip of your source code. refactorX checks the archive for unsafe content, keeps
          an exact copy for this review and skips dependencies, build output and secret files.
          Remove passwords and keys before uploading.
        </p>
      </div>
      <label
        className="dropzone"
        data-active={dragging}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => {
          setDragging(false);
        }}
        onDrop={onDrop}
      >
        <Icon name="upload" size={28} />
        <strong>Drop a .zip file here or choose one</strong>
        <span className="muted small">
          {policy.data
            ? `Up to ${formatBytes(policy.data.limits.max_upload_bytes)}`
            : "Loading limits…"}
        </span>
        <input
          ref={input}
          type="file"
          accept=".zip,application/zip"
          className="visually-hidden"
          aria-label="ZIP archive to upload"
          disabled={busy}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void upload(file);
            event.target.value = "";
          }}
        />
      </label>
      <div aria-live="polite" className="stack">
        {phase === "uploading" ? (
          <div>
            <div className="row small secondary">Uploading… {Math.round(progress * 100)}%</div>
            <div
              className="progress"
              role="progressbar"
              aria-valuenow={Math.round(progress * 100)}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <span style={{ width: `${progress * 100}%` }} />
            </div>
          </div>
        ) : null}
        {phase === "validating" ? (
          <div className="row small secondary">
            <span className="pulse-dot" /> Checking the archive and preparing your code…
          </div>
        ) : null}
        {error ? <Alert tone="bad">{error}</Alert> : null}
        {result?.state === "READY" && result.snapshot_id ? (
          <div className="success-panel" data-testid="upload-ready">
            <p>
              <Icon name="check" size={16} /> <strong>Your code is ready for review.</strong>
            </p>
            <div className="row">
              <StartReviewButton projectId={projectId} snapshotId={result.snapshot_id} />
              <a
                className="btn btn-ghost"
                href={`#/snapshots/${result.snapshot_id}`}
                data-testid="snapshot-link"
              >
                See what was uploaded
              </a>
            </div>
          </div>
        ) : null}
        {result && result.state !== "READY" ? (
          <Alert tone="bad">
            <p data-testid="intake-rejection">
              <strong>
                {result.state === "REJECTED"
                  ? "This archive was not accepted"
                  : "The upload could not be processed"}
                :
              </strong>{" "}
              {result.error_message}
            </p>
            {Array.isArray(violations) ? (
              <ul className="small">
                {(violations as { entry: string; message: string }[]).map((v) => (
                  <li key={`${v.entry}-${v.message}`}>
                    <code>{v.entry}</code> — {v.message}
                  </li>
                ))}
              </ul>
            ) : null}
            {result.error_code ? (
              <p className="small muted">
                Reason code: <code>{result.error_code}</code>
              </p>
            ) : null}
          </Alert>
        ) : null}
      </div>
    </section>
  );
}

function LocalRunner({ projectId }: { projectId: string }) {
  const command = `uv run crp-runner capture /path/to/folder --project-id ${projectId} --token-file .local/secrets/local-api-token --scan`;
  return (
    <Disclosure summary="For developers: upload a folder from this machine">
      <p className="small secondary">
        The local runner reads only the folder you name, skips secrets, version-control data and
        build output before anything is sent, shows what it will upload and asks for confirmation.
        It never changes the folder or runs its scripts. Add <code>--dry-run</code> to preview.
      </p>
      <CopyBlock label="Local runner command" text={command} />
    </Disclosure>
  );
}

function UploadsTable({ snapshots, projectId }: { snapshots: Snapshot[]; projectId: string }) {
  if (snapshots.length === 0)
    return <Empty title="No uploads yet">Upload a ZIP of your code to get started.</Empty>;
  return (
    <div className="table-wrap">
      <table className="data-table">
        <caption className="visually-hidden">Code uploads</caption>
        <thead>
          <tr>
            <th scope="col">Upload</th>
            <th scope="col" className="num">
              Files reviewed
            </th>
            <th scope="col" className="num">
              Skipped
            </th>
            <th scope="col">Uploaded</th>
            <th scope="col">
              <span className="visually-hidden">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {snapshots.map((snapshot) => (
            <tr key={snapshot.id}>
              <th scope="row">
                <a href={`#/snapshots/${snapshot.id}`}>{snapshot.source_name}</a>
                <div className="muted small">
                  {snapshot.source_mode === "local_runner" ? "Folder upload" : "ZIP upload"} ·{" "}
                  {formatBytes(snapshot.total_bytes)}
                </div>
              </th>
              <td className="num">{snapshot.analyzable_count}</td>
              <td className="num">{snapshot.excluded_count}</td>
              <td className="nowrap">{formatRelative(snapshot.frozen_at)}</td>
              <td>
                <StartReviewButton
                  projectId={projectId}
                  snapshotId={snapshot.id}
                  label="Review"
                  primary={false}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ReviewsTable({ scans }: { scans: Scan[] }) {
  if (scans.length === 0)
    return <Empty title="No reviews yet">Upload code, then start a review.</Empty>;
  const numbers = reviewNumbers(scans);
  return (
    <div className="table-wrap">
      <table className="data-table">
        <caption className="visually-hidden">Reviews</caption>
        <thead>
          <tr>
            <th scope="col">Review</th>
            <th scope="col">Status</th>
            <th scope="col" className="num">
              Findings
            </th>
            <th scope="col">Started</th>
          </tr>
        </thead>
        <tbody>
          {scans.map((scan) => (
            <tr key={scan.id}>
              <th scope="row">
                <a href={`#/scans/${scan.id}`}>{reviewLabel(numbers, scan.id)}</a>
              </th>
              <td>
                <StatusBadge state={scan.state} />
              </td>
              <td className="num">{findingTotal(scan.summary) ?? "—"}</td>
              <td className="nowrap">{formatDate(scan.started_at ?? scan.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ProjectPage({
  projectId,
  tab: requested,
  check = null,
  view = null,
}: {
  projectId: string;
  tab: string;
  check?: string | null;
  view?: string | null;
}) {
  const { options, principal } = useSession();
  const [deleting, setDeleting] = useState(false);
  const tab = TAB_ALIASES[requested] ?? requested;
  const insightsView = VIEW_ALIASES[requested] ?? view;
  const overview = useAsync((signal) => fetchProjectOverview(projectId, signal), [projectId]);
  const snapshots = useAsync((signal) => listSnapshots(projectId, signal), [projectId, tab]);
  const scans = useAsync((signal) => listScans(projectId, signal), [projectId, tab]);
  const project = overview.data?.project;
  if (overview.error) {
    const notFound = overview.error.includes("not found");
    return <Alert tone="bad">{notFound ? "This project was not found." : overview.error}</Alert>;
  }
  if (!overview.data || !project) return <Loading />;
  const latest = overview.data.latest_scan;
  const snapshot = overview.data.latest_snapshot;
  const base = `#/projects/${projectId}`;
  const member =
    principal?.workspaces.some(
      (w) => w.workspace_id === project.workspace_id && w.role !== "viewer",
    ) ?? false;
  return (
    <>
      <PageHeader
        eyebrow={<a href="#/projects">Projects</a>}
        title={
          <span className="row">
            {project.name}
            {project.origin === "synthetic_fixture" ? (
              <span className="badge badge-neutral">Sample</span>
            ) : null}
          </span>
        }
        sub={project.description || undefined}
        actions={
          <>
            {snapshot ? (
              <StartReviewButton
                projectId={projectId}
                snapshotId={snapshot.id}
                label="Review latest upload"
                primary={false}
              />
            ) : null}
            <a className="btn btn-primary" href={`${base}?tab=uploads`}>
              <Icon name="upload" size={16} /> Upload code
            </a>
          </>
        }
      />
      <Tabs
        current={tab}
        items={[
          { id: "overview", label: "Overview", href: base },
          { id: "issues", label: "Issues", href: `${base}?tab=issues` },
          { id: "insights", label: "Insights", href: `${base}?tab=insights` },
          { id: "fixes", label: "Fixes", href: `${base}?tab=fixes` },
          { id: "uploads", label: "Uploads", href: `${base}?tab=uploads` },
          ...(member ? [{ id: "settings", label: "Settings", href: `${base}?tab=settings` }] : []),
        ]}
      />
      {tab === "issues" ? (
        <IssuesView key={check ?? ""} projectId={projectId} check={check} />
      ) : null}
      {tab === "insights" ? (
        <InsightsView projectId={projectId} snapshotId={snapshot?.id ?? null} view={insightsView} />
      ) : null}
      {tab === "fixes" ? (
        <div className="stack">
          <WorkspacesView projectId={projectId} hasUpload={snapshot !== null} />
          <FixesView projectId={projectId} />
        </div>
      ) : null}
      {tab === "overview" ? (
        <div className="split">
          <section className="card" aria-labelledby="latest-title">
            <div className="card-head">
              <h2 id="latest-title" className="card-title">
                Latest review
              </h2>
              {latest ? <StatusBadge state={latest.state} /> : null}
            </div>
            {latest ? (
              <div className="stack">
                <p className="small secondary">
                  {findingTotal(latest.summary) ?? 0} findings ·{" "}
                  {formatRelative(latest.finished_at ?? latest.created_at)}
                  {snapshot ? ` · ${plural(snapshot.analyzable_count, "file")} reviewed` : ""}
                </p>
                <SeverityBars counts={severityCounts(latest.summary?.by_severity)} />
                <div className="row">
                  <a className="btn btn-primary" href={`#/scans/${latest.id}`}>
                    See the findings <Icon name="arrow" size={15} />
                  </a>
                  <a className="btn btn-ghost" href={`${base}?tab=issues`}>
                    Tracked issues
                  </a>
                </div>
              </div>
            ) : (
              <Empty title={snapshot ? "Ready for its first review" : "No code uploaded yet"}>
                {snapshot ? (
                  <StartReviewButton projectId={projectId} snapshotId={snapshot.id} />
                ) : (
                  <>
                    <p>Upload a ZIP of your source code, then start a review.</p>
                    <a className="btn btn-primary" href={`${base}?tab=uploads`}>
                      Upload code
                    </a>
                  </>
                )}
              </Empty>
            )}
          </section>
          <HealthSummary projectId={projectId} />
        </div>
      ) : null}
      {tab === "uploads" ? (
        <div className="stack">
          <ZipUpload projectId={projectId} />
          {isLocalDevelopment(options) ? <LocalRunner projectId={projectId} /> : null}
          <section className="card stack" aria-labelledby="uploads-title">
            <h2 id="uploads-title" className="card-title">
              Uploads ({overview.data.snapshot_count})
            </h2>
            {snapshots.error ? <Alert tone="bad">{snapshots.error}</Alert> : null}
            {snapshots.data ? (
              <UploadsTable snapshots={snapshots.data} projectId={projectId} />
            ) : (
              <Loading />
            )}
          </section>
          <section className="card stack" aria-labelledby="reviews-title">
            <h2 id="reviews-title" className="card-title">
              Reviews ({overview.data.scan_count})
            </h2>
            {scans.error ? <Alert tone="bad">{scans.error}</Alert> : null}
            {scans.data ? <ReviewsTable scans={scans.data} /> : <Loading />}
          </section>
          <GitHubView projectId={projectId} workspaceId={project.workspace_id} part="reviews" />
        </div>
      ) : null}
      {tab === "settings" && member ? (
        <div className="stack">
          <AiSettings projectId={projectId} />
          <GitHubView projectId={projectId} workspaceId={project.workspace_id} />
          <section className="card stack" aria-labelledby="danger-title">
            <h2 id="danger-title" className="card-title">
              Delete this project
            </h2>
            <p className="card-sub">
              Deletes its uploads, reviews, issues, workspaces and settings. This cannot be undone.
            </p>
            <div className="row">
              <button
                type="button"
                className="btn btn-sm btn-danger"
                onClick={() => {
                  setDeleting(true);
                }}
              >
                <Icon name="trash" size={14} /> Delete project
              </button>
            </div>
            <DeleteProjectDialog
              projectId={projectId}
              projectName={project.name}
              open={deleting}
              onClose={() => {
                setDeleting(false);
              }}
              onDeleted={() => {
                navigate("#/projects");
              }}
            />
          </section>
        </div>
      ) : null}
    </>
  );
}
