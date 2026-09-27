import { useState } from "react";

import { describeError, type FileEntry } from "../api/client";
import { fetchSnapshot, listFiles, startScan } from "../api/endpoints";
import { CategoryBars } from "../components/Charts";
import { Alert, Empty, Loading, PageHeader, Tabs } from "../components/Common";
import { Icon } from "../components/Icon";
import { StatusBadge } from "../components/Status";
import { formatBytes, formatDate, shortHash, titleCase } from "../lib/format";
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

const DISPOSITIONS = ["", "ANALYZABLE", "EXCLUDED", "BINARY", "OVERSIZED"] as const;

function dispositionBadge(value: string) {
  const tone = value === "ANALYZABLE" ? "ok" : value === "EXCLUDED" ? "warn" : "neutral";
  return <span className={`badge badge-${tone}`}>{titleCase(value)}</span>;
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
            Manifest
          </h2>
          <p className="card-sub">
            Every submitted entry is accounted for. Excluded means not stored and not reviewed.
          </p>
        </div>
        <span className="muted small">
          {page.data ? `${page.data.total.toLocaleString()} entries` : ""}
        </span>
      </div>
      <div className="row">
        {DISPOSITIONS.map((value) => (
          <button
            key={value || "all"}
            type="button"
            className="pill-toggle"
            aria-pressed={disposition === value}
            onClick={() => {
              setDisposition(value);
            }}
          >
            {value ? titleCase(value) : "All"}
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
      {page.data && rows.length === 0 ? <Empty title="No matching entries" /> : null}
      {rows.length > 0 ? (
        <div className="table-wrap" style={{ maxHeight: 520 }}>
          <table className="data-table">
            <caption className="visually-hidden">Snapshot manifest entries</caption>
            <thead>
              <tr>
                <th scope="col">Path</th>
                <th scope="col">Disposition</th>
                <th scope="col">Language</th>
                <th scope="col" className="num">
                  Size
                </th>
                <th scope="col" className="num">
                  Lines
                </th>
                <th scope="col">Parse</th>
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
                      <div className="muted small">{file.reason.replaceAll("_", " ")}</div>
                    ) : null}
                  </td>
                  <td>{file.language ?? "—"}</td>
                  <td className="num">{formatBytes(file.size_bytes)}</td>
                  <td className="num">{file.line_count ?? "—"}</td>
                  <td>
                    {file.parse_status ? (
                      <StatusBadge
                        state={file.parse_status === "OK" ? "SUCCEEDED" : file.parse_status}
                        label={
                          file.parse_status === "PARTIAL"
                            ? `Partial (${file.parse_error_count})`
                            : titleCase(file.parse_status)
                        }
                      />
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
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

  return (
    <>
      <PageHeader
        eyebrow={
          <>
            <a href={`#/projects/${data.project_id}`}>Project</a> <span aria-hidden="true">/</span>{" "}
            Snapshot
          </>
        }
        title={
          <>
            Snapshot{" "}
            <span className="gradient-text mono">{shortHash(data.manifest_sha256, 12)}</span>
          </>
        }
        sub="Immutable, content-identified capture. Review the scope before scanning: only analyzable files are reviewed."
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
            {busy ? "Starting…" : "Start baseline scan"}
          </button>
        }
      />
      {error ? <Alert tone="bad">{error}</Alert> : null}
      <Tabs
        current={tab}
        items={[
          { id: "scope", label: "Scope & files", href: `#/snapshots/${snapshotId}` },
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
            <Alert tone="warn">
              <p>
                This snapshot contains agent instruction files ({agents.join(", ")}). They are
                treated as <strong>untrusted data</strong>: they cannot change platform rules, tools
                or policy.
              </p>
            </Alert>
          ) : null}
          <div className="grid grid-3">
            <section className="card" aria-labelledby="identity-title">
              <h2 id="identity-title" className="card-title" style={{ marginBottom: 12 }}>
                Identity
              </h2>
              <dl className="kv">
                <dt>Manifest</dt>
                <dd className="hash" data-testid="manifest-sha">
                  {data.manifest_sha256}
                </dd>
                <dt>Source</dt>
                <dd>
                  {data.source_mode === "local_runner" ? "Local folder" : "ZIP upload"} ·{" "}
                  {data.source_name}
                </dd>
                <dt>Policy</dt>
                <dd className="mono">{data.policy_version}</dd>
                <dt>Frozen</dt>
                <dd>{formatDate(data.frozen_at)}</dd>
                <dt>Git commit</dt>
                <dd className="muted">{data.git_commit ?? "none (content-identified snapshot)"}</dd>
              </dl>
            </section>
            <section className="card" aria-labelledby="scope-title">
              <h2 id="scope-title" className="card-title" style={{ marginBottom: 12 }}>
                Scope
              </h2>
              <dl className="kv">
                <dt>Entries</dt>
                <dd>{data.file_count.toLocaleString()}</dd>
                <dt>Analyzable</dt>
                <dd>{data.analyzable_count.toLocaleString()}</dd>
                <dt>Excluded</dt>
                <dd>{data.excluded_count.toLocaleString()}</dd>
                <dt>Size</dt>
                <dd>{formatBytes(data.total_bytes)}</dd>
              </dl>
              {inventory.reasons && Object.keys(inventory.reasons).length > 0 ? (
                <ul className="legend" aria-label="Exclusion and classification reasons">
                  {Object.entries(inventory.reasons).map(([reason, count]) => (
                    <li key={reason}>
                      {reason.replaceAll("_", " ")} <strong>{count}</strong>
                    </li>
                  ))}
                </ul>
              ) : null}
            </section>
            <section className="card" aria-labelledby="lang-title">
              <h2 id="lang-title" className="card-title" style={{ marginBottom: 12 }}>
                Files by language
              </h2>
              <CategoryBars counts={languageFiles} />
            </section>
          </div>
          <section className="card" aria-labelledby="tech-title">
            <div className="card-head">
              <div>
                <h2 id="tech-title" className="card-title">
                  Technology indicators
                </h2>
                <p className="card-sub">
                  Read from build descriptors as data — nothing was executed.
                </p>
              </div>
            </div>
            {(inventory.indicators ?? []).length === 0 ? (
              <p className="muted small">No build descriptors recognised.</p>
            ) : (
              <div className="table-wrap">
                <table className="data-table">
                  <caption className="visually-hidden">Detected technologies</caption>
                  <thead>
                    <tr>
                      <th scope="col">Technology</th>
                      <th scope="col">Version</th>
                      <th scope="col">Confidence</th>
                      <th scope="col">Evidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(inventory.indicators ?? []).map((indicator) => (
                      <tr key={`${indicator.name}-${indicator.evidence_path}`}>
                        <th scope="row">{indicator.name}</th>
                        <td className="mono">{indicator.version ?? "—"}</td>
                        <td>
                          <span
                            className={`badge badge-${indicator.confidence === "declared" ? "ok" : "neutral"}`}
                          >
                            {indicator.confidence}
                          </span>
                        </td>
                        <td className="small">
                          <code>{indicator.evidence_path}</code>{" "}
                          <span className="muted">{indicator.evidence}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
          <FileExplorer snapshotId={snapshotId} />
        </>
      )}
    </>
  );
}
