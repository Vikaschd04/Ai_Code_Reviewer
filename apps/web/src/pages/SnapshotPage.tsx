import { useState } from "react";

import { describeError, type FileEntry } from "../api/client";
import { fetchSnapshot, listFiles, startScan } from "../api/endpoints";
import { CategoryBars } from "../components/Charts";
import { Alert, Disclosure, Empty, Loading, PageHeader, Tabs } from "../components/Common";
import { Icon } from "../components/Icon";
import { formatBytes, formatDate, titleCase } from "../lib/format";
import { fileReason, plural } from "../lib/labels";
import { navigate } from "../lib/router";
import { useAsync } from "../lib/useAsync";
import { ArchitectureView } from "./ArchitectureView";

interface Indicator {
  name: string;
  kind: string;
  version: string | null;
  confidence: string;
  evidence_path: string;
  evidence: string;
}

interface Inventory {
  languages?: { language: string; files: number; lines: number }[];
  indicators?: Indicator[];
  reasons?: Record<string, number>;
  dispositions?: Record<string, number>;
  agent_instruction_files?: string[];
  notes?: string[];
}

const DISPOSITIONS: { value: string; label: string }[] = [
  { value: "", label: "All" },
  { value: "ANALYZABLE", label: "Reviewed" },
  { value: "EXCLUDED", label: "Skipped" },
  { value: "BINARY", label: "Binary" },
  { value: "OVERSIZED", label: "Too large" },
];

function dispositionBadge(value: string) {
  const tone = value === "ANALYZABLE" ? "ok" : value === "EXCLUDED" ? "warn" : "neutral";
  const label = DISPOSITIONS.find((d) => d.value === value)?.label ?? titleCase(value);
  return <span className={`badge badge-${tone}`}>{label}</span>;
}

function FileExplorer({ snapshotId }: { snapshotId: string }) {
  const [disposition, setDisposition] = useState("");
  const [query, setQuery] = useState("");
  const [extra, setExtra] = useState<FileEntry[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const page = useAsync(
    (signal) =>
      listFiles(snapshotId, { disposition, q: query }, signal).then((result) => {
        setExtra([]);
        setCursor(result.next_cursor);
        return result;
      }),
    [snapshotId, disposition, query],
  );
  const rows = [...(page.data?.items ?? []), ...extra];

  async function more() {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const next = await listFiles(snapshotId, { disposition, q: query, cursor });
      setExtra((previous) => [...previous, ...next.items]);
      setCursor(next.next_cursor);
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <section className="card stack" aria-labelledby="files-title">
      <div className="card-head" style={{ marginBottom: 0 }}>
        <div>
          <h2 id="files-title" className="card-title">
            Files
          </h2>
          <p className="card-sub">
            Every file in the upload and whether it is reviewed. Skipped files are not stored.
          </p>
        </div>
        <span className="muted small">{page.data ? plural(page.data.total, "file") : ""}</span>
      </div>
      <div className="row">
        {DISPOSITIONS.map((item) => (
          <button
            key={item.value || "all"}
            type="button"
            className="pill-toggle"
            aria-pressed={disposition === item.value}
            onClick={() => {
              setDisposition(item.value);
            }}
          >
            {item.label}
          </button>
        ))}
        <input
          type="search"
          aria-label="Filter files by path"
          placeholder="Filter by path…"
          style={{ maxWidth: 280, marginLeft: "auto" }}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
          }}
        />
      </div>
      {page.error ? <Alert tone="bad">{page.error}</Alert> : null}
      {page.loading && !page.data ? <Loading /> : null}
      {page.data && rows.length === 0 ? <Empty title="No matching files" /> : null}
      {rows.length > 0 ? (
        <div className="table-wrap" style={{ maxHeight: 520 }}>
          <table className="data-table">
            <caption className="visually-hidden">Files in this upload</caption>
            <thead>
              <tr>
                <th scope="col">File</th>
                <th scope="col">Status</th>
                <th scope="col">Language</th>
                <th scope="col" className="num">
                  Size
                </th>
                <th scope="col" className="num">
                  Lines
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((file) => (
                <tr key={file.id} data-testid="manifest-row">
                  <th scope="row" className="mono" style={{ fontWeight: 500 }}>
                    {file.path}
                  </th>
                  <td>
                    {dispositionBadge(file.disposition)}
                    {file.reason ? (
                      <div className="muted small">{fileReason(file.reason)}</div>
                    ) : null}
                    {file.parse_status === "PARTIAL" || file.parse_status === "FAILED" ? (
                      <div className="muted small">Some lines could not be read</div>
                    ) : null}
                  </td>
                  <td>{file.language ?? "—"}</td>
                  <td className="num">{formatBytes(file.size_bytes)}</td>
                  <td className="num">{file.line_count ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {cursor ? (
        <button
          type="button"
          className="btn btn-ghost"
          disabled={loadingMore}
          onClick={() => void more()}
        >
          {loadingMore ? "Loading…" : "Load more"}
        </button>
      ) : null}
    </section>
  );
}

export function SnapshotPage({ snapshotId, tab = "scope" }: { snapshotId: string; tab?: string }) {
  const snapshot = useAsync((signal) => fetchSnapshot(snapshotId, signal), [snapshotId]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (snapshot.error) return <Alert tone="bad">{snapshot.error}</Alert>;
  if (!snapshot.data) return <Loading />;
  const data = snapshot.data;
  const inventory = (data.inventory ?? {}) as Inventory;
  const languageFiles = Object.fromEntries(
    (inventory.languages ?? []).map((l) => [l.language, l.files]),
  );
  const agents = inventory.agent_instruction_files ?? [];
  const indicators = inventory.indicators ?? [];
  const skippedReasons = Object.entries(inventory.reasons ?? {});
  return (
    <>
      <PageHeader
        eyebrow={
          <>
            <a href={`#/projects/${data.project_id}`}>Project</a> <span aria-hidden="true">/</span>{" "}
            Upload
          </>
        }
        title={data.source_name}
        sub={`Uploaded ${formatDate(data.frozen_at)} · ${plural(data.analyzable_count, "file")} to review · ${plural(data.excluded_count, "file")} skipped`}
        actions={
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              setError(null);
              startScan(data.project_id, data.id).then(
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
            <Icon name="scan" size={16} />
            {busy ? "Starting…" : "Start review"}
          </button>
        }
      />
      {error ? <Alert tone="bad">{error}</Alert> : null}
      <Tabs
        current={tab}
        items={[
          { id: "scope", label: "What's inside", href: `#/snapshots/${snapshotId}` },
          {
            id: "architecture",
            label: "Architecture",
            href: `#/snapshots/${snapshotId}?tab=architecture`,
          },
        ]}
      />
      {tab === "architecture" ? <ArchitectureView snapshotId={snapshotId} /> : null}
      {tab === "architecture" ? null : (
        <>
          {agents.length > 0 ? (
            <Alert tone="info">
              <p>
                This upload contains AI-assistant instruction files ({agents.join(", ")}). refactorX
                reads them as plain text only; they cannot change how your code is reviewed.
              </p>
            </Alert>
          ) : null}
          <div className="grid grid-3">
            <section className="card" aria-labelledby="scope-title">
              <h2 id="scope-title" className="card-title" style={{ marginBottom: 12 }}>
                Files
              </h2>
              <dl className="kv">
                <dt>To review</dt>
                <dd>{data.analyzable_count.toLocaleString()}</dd>
                <dt>Skipped</dt>
                <dd>{data.excluded_count.toLocaleString()}</dd>
                <dt>Total size</dt>
                <dd>{formatBytes(data.total_bytes)}</dd>
              </dl>
              {skippedReasons.length > 0 ? (
                <ul className="reason-list" aria-label="Why files were skipped or classified">
                  {skippedReasons.map(([reason, count]) => (
                    <li key={reason}>
                      <span>{fileReason(reason)}</span> <strong>{count}</strong>
                    </li>
                  ))}
                </ul>
              ) : null}
            </section>
            <section className="card" aria-labelledby="lang-title">
              <h2 id="lang-title" className="card-title" style={{ marginBottom: 12 }}>
                Languages
              </h2>
              <CategoryBars counts={languageFiles} />
            </section>
            <section className="card" aria-labelledby="tech-title">
              <h2 id="tech-title" className="card-title" style={{ marginBottom: 12 }}>
                Technologies
              </h2>
              {indicators.length === 0 ? (
                <p className="muted small">No frameworks or build tools recognised.</p>
              ) : (
                <ul className="chip-list" aria-label="Detected technologies">
                  {indicators.map((indicator) => (
                    <li key={`${indicator.name}-${indicator.evidence_path}`} className="chip">
                      {indicator.name}
                      {indicator.version ? (
                        <span className="muted"> {indicator.version}</span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
              <p className="hint">Read from build files; nothing in your code was run.</p>
            </section>
          </div>
          <FileExplorer snapshotId={snapshotId} />
          <Disclosure testId="snapshot-technical">
            <dl className="kv">
              <dt>Snapshot fingerprint</dt>
              <dd className="hash" data-testid="manifest-sha">
                {data.manifest_sha256}
              </dd>
              <dt>Source</dt>
              <dd>
                {data.source_mode === "local_runner" ? "Local folder" : "ZIP upload"} ·{" "}
                {data.source_name}
              </dd>
              <dt>Scope policy</dt>
              <dd className="mono">{data.policy_version}</dd>
              <dt>Git commit</dt>
              <dd className="muted">{data.git_commit ?? "none (identified by content)"}</dd>
            </dl>
            {indicators.length > 0 ? (
              <div className="table-wrap">
                <table className="data-table">
                  <caption className="visually-hidden">Technology evidence</caption>
                  <thead>
                    <tr>
                      <th scope="col">Technology</th>
                      <th scope="col">Version</th>
                      <th scope="col">Confidence</th>
                      <th scope="col">Evidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {indicators.map((indicator) => (
                      <tr key={`${indicator.name}-${indicator.evidence_path}`}>
                        <th scope="row">{indicator.name}</th>
                        <td className="mono">{indicator.version ?? "—"}</td>
                        <td>{indicator.confidence}</td>
                        <td className="small">
                          <code>{indicator.evidence_path}</code>{" "}
                          <span className="muted">{indicator.evidence}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </Disclosure>
        </>
      )}
    </>
  );
}
