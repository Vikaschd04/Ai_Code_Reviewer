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
import { Alert, CopyBlock, Empty, Loading, PageHeader, Tabs } from "../components/Common";
import { Icon } from "../components/Icon";
import { SeverityBars, severityCounts } from "../components/Severity";
import { StatusBadge, findingTotal } from "../components/Status";
import { formatBytes, formatDate, formatRelative, shortHash } from "../lib/format";
import { navigate } from "../lib/router";
import { useAsync } from "../lib/useAsync";
import { ArchitectureView } from "./ArchitectureView";
import { IssuesView } from "./IssuesView";

type UploadPhase = "idle" | "uploading" | "validating" | "done";

async function waitForIntake(id: string): Promise<Intake> {
  for (let attempt = 0; attempt < 600; attempt++) {
    const intake = await fetchIntake(id);
    if (["READY", "REJECTED", "FAILED", "CANCELED"].includes(intake.state)) return intake;
    await new Promise((resolve) => setTimeout(resolve, 700));
  }
  throw new Error("Validation is taking longer than expected; check the intake later.");
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
      setError("Choose a .zip archive.");
      return;
    }
    if (limit !== null && file.size > limit) {
      setError(`The file is ${formatBytes(file.size)}; the upload limit is ${formatBytes(limit)}.`);
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
  return (
    <section className="card stack" aria-labelledby="zip-title">
      <div>
        <h2 id="zip-title" className="card-title">
          <Icon name="upload" size={16} /> Upload a ZIP archive
        </h2>
        <p className="card-sub">
          The archive is streamed to this server, validated (paths, symlinks, collisions, size and
          compression limits) and frozen into an immutable snapshot. Bytes reach the server before
          exclusions apply — remove secrets first, or use the local runner.
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
        <strong>Drop a .zip here or choose a file</strong>
        <span className="muted small">
          {policy.data
            ? `Up to ${formatBytes(policy.data.limits.max_upload_bytes)} · ${policy.data.limits.max_entries.toLocaleString()} entries · policy ${policy.data.version}`
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
            <span className="pulse-dot" /> Validating and freezing the snapshot…
          </div>
        ) : null}
        {error ? <Alert tone="bad">{error}</Alert> : null}
        {result?.state === "READY" && result.snapshot_id ? (
          <Alert tone="info">
            <p>
              <strong>Snapshot ready.</strong> Archive sha256{" "}
              <span className="hash">{shortHash(result.archive_sha256, 16)}</span>.{" "}
              <a href={`#/snapshots/${result.snapshot_id}`} data-testid="snapshot-link">
                Review scope and start a scan →
              </a>
            </p>
          </Alert>
        ) : null}
        {result && result.state !== "READY" ? (
          <Alert tone="bad">
            <p data-testid="intake-rejection">
              <strong>
                {result.state === "REJECTED"
                  ? "Archive rejected"
                  : `Intake ${result.state.toLowerCase()}`}
                :
              </strong>{" "}
              {result.error_message} <code>{result.error_code}</code>
            </p>
            {Array.isArray(
              (result.error_details as { violations?: unknown } | null)?.violations,
            ) ? (
              <ul className="small">
                {(
                  result.error_details as { violations: { entry: string; message: string }[] }
                ).violations.map((v) => (
                  <li key={`${v.entry}-${v.message}`}>
                    <code>{v.entry}</code> — {v.message}
                  </li>
                ))}
              </ul>
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
    <section className="card stack" aria-labelledby="runner-title">
      <div>
        <h2 id="runner-title" className="card-title">
          <Icon name="terminal" size={16} /> Capture a local folder
        </h2>
        <p className="card-sub">
          Run the local runner on your machine. It reads only the folder you name, skips secrets,
          VCS metadata and dependency/build output <em>before</em> anything is sent, shows what it
          will upload, and asks for confirmation.{" "}
          <strong>The listed source files are uploaded to this platform.</strong> It never modifies
          the folder or runs its scripts.
        </p>
      </div>
      <CopyBlock label="Local runner command" text={command} />
      <p className="hint">
        Add <code>--dry-run</code> first to preview the capture without sending anything. A browser
        cannot read local folders by path; this is why capture runs as a separate command.
      </p>
    </section>
  );
}

function SnapshotsTable({ snapshots, projectId }: { snapshots: Snapshot[]; projectId: string }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (snapshots.length === 0)
    return <Empty title="No snapshots yet">Upload a ZIP or capture a folder.</Empty>;
  return (
    <>
      {error ? <Alert tone="bad">{error}</Alert> : null}
      <div className="table-wrap">
        <table className="data-table">
          <caption className="visually-hidden">Frozen snapshots</caption>
          <thead>
            <tr>
              <th scope="col">Snapshot</th>
              <th scope="col">Source</th>
              <th scope="col" className="num">
                Analyzable
              </th>
              <th scope="col" className="num">
                Excluded
              </th>
              <th scope="col">Frozen</th>
              <th scope="col">
                <span className="visually-hidden">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {snapshots.map((snapshot) => (
              <tr key={snapshot.id}>
                <th scope="row">
                  <a href={`#/snapshots/${snapshot.id}`} className="hash">
                    {shortHash(snapshot.manifest_sha256, 16)}
                  </a>
                </th>
                <td>
                  {snapshot.source_mode === "local_runner" ? "Local folder" : "ZIP"} ·{" "}
                  {snapshot.source_name}
                </td>
                <td className="num">{snapshot.analyzable_count}</td>
                <td className="num">{snapshot.excluded_count}</td>
                <td>{formatRelative(snapshot.frozen_at)}</td>
                <td>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    disabled={busy !== null}
                    onClick={() => {
                      setBusy(snapshot.id);
                      startScan(projectId, snapshot.id).then(
                        (scan) => {
                          navigate(`#/scans/${scan.id}`);
                        },
                        (caught: unknown) => {
                          setError(describeError(caught));
                          setBusy(null);
                        },
                      );
                    }}
                  >
                    <Icon name="scan" size={14} /> Scan
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function ScansTable({ scans }: { scans: Scan[] }) {
  if (scans.length === 0) return <Empty title="No scans yet">Start one from a snapshot.</Empty>;
  return (
    <div className="table-wrap">
      <table className="data-table">
        <caption className="visually-hidden">Scans</caption>
        <thead>
          <tr>
            <th scope="col">Scan</th>
            <th scope="col">State</th>
            <th scope="col" className="num">
              Findings
            </th>
            <th scope="col">Snapshot</th>
            <th scope="col">Started</th>
          </tr>
        </thead>
        <tbody>
          {scans.map((scan) => (
            <tr key={scan.id}>
              <th scope="row">
                <a href={`#/scans/${scan.id}`} className="hash">
                  {shortHash(scan.id, 8)}
                </a>
              </th>
              <td>
                <StatusBadge state={scan.state} />
              </td>
              <td className="num">{findingTotal(scan.summary) ?? "—"}</td>
              <td className="hash">{shortHash(scan.manifest_sha256, 12)}</td>
              <td>{formatDate(scan.started_at ?? scan.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ProjectPage({ projectId, tab }: { projectId: string; tab: string }) {
  const overview = useAsync((signal) => fetchProjectOverview(projectId, signal), [projectId]);
  const snapshots = useAsync((signal) => listSnapshots(projectId, signal), [projectId, tab]);
  const scans = useAsync((signal) => listScans(projectId, signal), [projectId, tab]);
  const project = overview.data?.project;
  if (overview.error) {
    const notFound = overview.error.includes("not found");
    return (
      <Alert tone="bad">{notFound ? "Project not found or not accessible." : overview.error}</Alert>
    );
  }
  if (!overview.data || !project) return <Loading />;
  const latest = overview.data.latest_scan;
  const base = `#/projects/${projectId}`;
  return (
    <>
      <PageHeader
        eyebrow={<a href="#/projects">Projects</a>}
        title={project.name}
        sub={
          project.origin === "synthetic_fixture"
            ? "Synthetic fixture project (labelled test data)."
            : project.description || undefined
        }
        actions={
          <a className="btn btn-primary" href={`${base}?tab=source`}>
            <Icon name="upload" size={16} /> Add source
          </a>
        }
      />
      <Tabs
        current={tab}
        items={[
          { id: "overview", label: "Overview", href: base },
          { id: "source", label: "Add source", href: `${base}?tab=source` },
          {
            id: "snapshots",
            label: `Snapshots (${overview.data.snapshot_count})`,
            href: `${base}?tab=snapshots`,
          },
          { id: "scans", label: `Scans (${overview.data.scan_count})`, href: `${base}?tab=scans` },
          { id: "issues", label: "Issues", href: `${base}?tab=issues` },
          { id: "architecture", label: "Architecture", href: `${base}?tab=architecture` },
        ]}
      />
      {tab === "issues" ? <IssuesView projectId={projectId} /> : null}
      {tab === "architecture" ? (
        overview.data.latest_snapshot ? (
          <>
            <p className="small secondary" style={{ margin: 0 }}>
              Latest snapshot{" "}
              <a
                href={`#/snapshots/${overview.data.latest_snapshot.id}?tab=architecture`}
                className="mono"
              >
                {shortHash(overview.data.latest_snapshot.manifest_sha256, 12)}
              </a>
            </p>
            <ArchitectureView snapshotId={overview.data.latest_snapshot.id} />
          </>
        ) : (
          <Empty title="No snapshot yet">
            <p>Add source and scan it to build the architecture graph.</p>
          </Empty>
        )
      ) : null}
      {tab === "overview" ? (
        <div className="split">
          <section className="card" aria-labelledby="latest-title">
            <div className="card-head">
              <h2 id="latest-title" className="card-title">
                Latest scan
              </h2>
              {latest ? <StatusBadge state={latest.state} /> : null}
            </div>
            {latest ? (
              <div className="stack">
                <SeverityBars counts={severityCounts(latest.summary?.by_severity)} />
                <a className="btn btn-primary" href={`#/scans/${latest.id}`}>
                  Open findings <Icon name="arrow" size={15} />
                </a>
              </div>
            ) : (
              <Empty title="Nothing scanned yet">
                <p>Add source to create a frozen snapshot, then start a baseline scan.</p>
                <a className="btn btn-primary" href={`${base}?tab=source`}>
                  Add source
                </a>
              </Empty>
            )}
          </section>
          <section className="card" aria-labelledby="about-title">
            <h2 id="about-title" className="card-title" style={{ marginBottom: 12 }}>
              About
            </h2>
            <dl className="kv">
              <dt>Slug</dt>
              <dd className="mono">{project.slug}</dd>
              <dt>Created</dt>
              <dd>{formatDate(project.created_at)}</dd>
              <dt>Latest snapshot</dt>
              <dd className="hash">
                {shortHash(overview.data.latest_snapshot?.manifest_sha256, 20)}
              </dd>
              <dt>Snapshot size</dt>
              <dd>{formatBytes(overview.data.latest_snapshot?.total_bytes)}</dd>
            </dl>
            {project.description ? <p className="secondary small">{project.description}</p> : null}
          </section>
        </div>
      ) : null}
      {tab === "source" ? (
        <div className="grid grid-2">
          <ZipUpload projectId={projectId} />
          <LocalRunner projectId={projectId} />
        </div>
      ) : null}
      {tab === "snapshots" ? (
        <section className="card">
          {snapshots.error ? <Alert tone="bad">{snapshots.error}</Alert> : null}
          {snapshots.data ? (
            <SnapshotsTable snapshots={snapshots.data} projectId={projectId} />
          ) : (
            <Loading />
          )}
        </section>
      ) : null}
      {tab === "scans" ? (
        <section className="card">
          {scans.error ? <Alert tone="bad">{scans.error}</Alert> : null}
          {scans.data ? <ScansTable scans={scans.data} /> : <Loading />}
        </section>
      ) : null}
    </>
  );
}
