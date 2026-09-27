import { useEffect, useState } from "react";

import {
  describeError,
  type ComparisonGroup,
  type EngineRun,
  type Finding,
  type Scan,
} from "../api/client";
import {
  cancelScan,
  compareScans,
  exportUrl,
  fetchScan,
  listCoverage,
  listFindings,
  listScans,
  startScan,
} from "../api/endpoints";
import { CategoryBars, CoverageMeter } from "../components/Charts";
import { Alert, Empty, Loading, PageHeader, Tabs } from "../components/Common";
import { Icon } from "../components/Icon";
import {
  SEVERITIES,
  SeverityBars,
  SeverityChip,
  SeverityMark,
  severityCounts,
} from "../components/Severity";
import { StatusBadge, findingTotal, isTerminalScan } from "../components/Status";
import { formatDuration, formatRelative, shortHash, titleCase } from "../lib/format";
import { navigate } from "../lib/router";
import { useAsync } from "../lib/useAsync";
import { findingLocation } from "./FindingPage";

const ENGINE_LABELS: Record<string, string> = {
  structure: "Structure (Tree-sitter)",
  graph: "Graph · relations",
  pmd: "PMD · Java",
  eslint: "ESLint · JS/TS",
  opengrep: "Opengrep · security rules",
  trivy: "Trivy · dependencies & secrets",
};
const ENGINES = ["structure", "graph", "pmd", "eslint", "opengrep", "trivy"];
const STAGE_LABELS: Record<string, string> = {
  structure: "Structure",
  graph: "Graph",
  pmd: "PMD",
  eslint: "ESLint",
  opengrep: "Opengrep",
  trivy: "Trivy",
};
const FINDING_ENGINES = ["pmd", "eslint", "opengrep", "trivy"];
const CATEGORIES = [
  "security",
  "dependencies",
  "correctness",
  "reliability",
  "performance",
  "maintainability",
  "coding_standards",
];

/** Subscribe to server-sent progress events; returns a counter that changes on every event. */
function useScanEvents(scanId: string, active: boolean): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!active) return;
    const source = new EventSource(`/v1/scans/${encodeURIComponent(scanId)}/events`);
    const bump = () => {
      setTick((value) => value + 1);
    };
    for (const kind of [
      "scan_started",
      "engine_started",
      "engine_finished",
      "scan_finished",
      "end",
    ]) {
      source.addEventListener(kind, bump);
    }
    source.addEventListener("end", () => {
      source.close();
    });
    const fallback = window.setInterval(bump, 4000);
    return () => {
      source.close();
      window.clearInterval(fallback);
    };
  }, [scanId, active]);
  return tick;
}

function Pipeline({ scan }: { scan: Scan }) {
  const runs = new Map(scan.engines.map((run) => [run.engine, run]));
  const stages = [
    {
      id: "snapshot",
      name: "Snapshot",
      state: "SUCCEEDED",
      meta: shortHash(scan.manifest_sha256, 10),
    },
    ...ENGINES.map((engine) => {
      const run = runs.get(engine);
      return {
        id: engine,
        name: STAGE_LABELS[engine] ?? engine,
        state: run?.state ?? (isTerminalScan(scan.state) ? "NOT_APPLICABLE" : "QUEUED"),
        meta: run ? `${run.files_eligible} eligible` : "waiting",
      };
    }),
    {
      id: "publish",
      name: "Published",
      state: isTerminalScan(scan.state) ? scan.state : "QUEUED",
      meta: isTerminalScan(scan.state) ? `${findingTotal(scan.summary) ?? 0} findings` : "pending",
    },
  ];
  return (
    <ol
      className="pipeline"
      aria-label="Scan pipeline"
      style={{ listStyle: "none", padding: 0, margin: 0 }}
    >
      {stages.map((stage) => (
        <li
          key={stage.id}
          className="stage"
          data-state={stage.state}
          data-testid={`stage-${stage.id}`}
        >
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span className="stage-name">{stage.name}</span>
            {stage.state === "RUNNING" ? <span className="pulse-dot" aria-hidden="true" /> : null}
          </div>
          <StatusBadge state={stage.state} />
          <span className="stage-meta">{stage.meta}</span>
        </li>
      ))}
    </ol>
  );
}

function EngineCard({ run }: { run: EngineRun }) {
  return (
    <article
      className="card stack"
      aria-labelledby={`engine-${run.engine}`}
      data-testid={`engine-${run.engine}`}
    >
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3 id={`engine-${run.engine}`} className="card-title">
          {ENGINE_LABELS[run.engine] ?? run.engine}
        </h3>
        <StatusBadge state={run.state} />
      </div>
      <CoverageMeter run={run} />
      <dl className="details">
        <div>
          <dt>Version</dt>
          <dd className="mono">{run.engine_version ?? "—"}</dd>
        </div>
        <div>
          <dt>Ruleset</dt>
          <dd className="mono">{run.ruleset_id ?? "—"}</dd>
        </div>
        <div>
          <dt>Findings</dt>
          <dd>{run.findings_count}</dd>
        </div>
        <div>
          <dt>Duration</dt>
          <dd>{formatDuration(run.duration_ms)}</dd>
        </div>
        <div>
          <dt>Rules</dt>
          <dd>
            {run.enabled_rule_count === null || run.enabled_rule_count === undefined
              ? run.engine === "trivy"
                ? "vuln DB + secrets"
                : "—"
              : run.enabled_rule_count}
          </dd>
        </div>
        <div>
          <dt>Cache</dt>
          <dd data-testid={`cache-${run.engine}`}>
            {run.cache_hits || run.cache_misses
              ? `${String(run.cache_hits)} reused · ${String(run.cache_misses)} run`
              : cacheNote(run)}
          </dd>
        </div>
      </dl>
      {typeof run.diagnostics?.db_updated_at === "string" ? (
        <p className="hint" style={{ margin: 0 }}>
          Vulnerability DB {formatRelative(run.diagnostics.db_updated_at)}
          {run.diagnostics.db_stale === true ? " · stale, refresh with make engines" : ""} · offline
        </p>
      ) : null}
      {run.error_message ? (
        <Alert tone={run.state === "UNAVAILABLE" || run.state === "FAILED" ? "bad" : "warn"}>
          <p>
            <code>{run.error_code}</code> {run.error_message}
          </p>
        </Alert>
      ) : null}
    </article>
  );
}

function cacheNote(run: EngineRun): string {
  const cache = run.diagnostics?.cache as { eligible?: boolean } | undefined;
  if (cache && cache.eligible === false) return "not cacheable";
  return "—";
}

function FindingsTab({ scanId, terminal }: { scanId: string; terminal: boolean }) {
  const [severity, setSeverity] = useState<string[]>([]);
  const [engine, setEngine] = useState("");
  const [category, setCategory] = useState("");
  const [issueStatus, setIssueStatus] = useState("");
  const [query, setQuery] = useState("");
  const [extra, setExtra] = useState<Finding[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const page = useAsync(
    (signal) =>
      listFindings(scanId, { severity, engine, category, issueStatus, q: query }, signal).then(
        (result) => {
          setExtra([]);
          setCursor(result.next_cursor);
          return result;
        },
      ),
    [scanId, severity.join(","), engine, category, issueStatus, query, terminal],
  );
  const rows = [...(page.data?.items ?? []), ...extra];

  return (
    <section className="card stack" aria-labelledby="findings-title">
      <div className="card-head" style={{ marginBottom: 0 }}>
        <h2 id="findings-title" className="card-title">
          Findings
        </h2>
        <span className="muted small" aria-live="polite">
          {page.data ? `${page.data.total} match${page.data.total === 1 ? "" : "es"}` : ""}
        </span>
      </div>
      <div className="row" role="group" aria-label="Finding filters">
        {SEVERITIES.map((name) => (
          <button
            key={name}
            type="button"
            className="pill-toggle"
            aria-pressed={severity.includes(name)}
            onClick={() => {
              setSeverity((current) =>
                current.includes(name) ? current.filter((s) => s !== name) : [...current, name],
              );
            }}
          >
            <SeverityMark severity={name} />
            {titleCase(name)}
          </button>
        ))}
        <select
          aria-label="Engine"
          value={engine}
          style={{ width: "auto" }}
          onChange={(event) => {
            setEngine(event.target.value);
          }}
        >
          <option value="">All engines</option>
          {FINDING_ENGINES.map((name) => (
            <option key={name} value={name}>
              {ENGINE_LABELS[name]}
            </option>
          ))}
        </select>
        <select
          aria-label="Issue status"
          value={issueStatus}
          style={{ width: "auto" }}
          onChange={(event) => {
            setIssueStatus(event.target.value);
          }}
        >
          <option value="">Any issue status</option>
          {["OPEN", "TRIAGED", "ACCEPTED_RISK", "FALSE_POSITIVE", "RESOLVED"].map((value) => (
            <option key={value} value={value}>
              {titleCase(value)}
            </option>
          ))}
        </select>
        <select
          aria-label="Category"
          value={category}
          style={{ width: "auto" }}
          onChange={(event) => {
            setCategory(event.target.value);
          }}
        >
          <option value="">All categories</option>
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {titleCase(c)}
            </option>
          ))}
        </select>
        <input
          type="search"
          aria-label="Search findings by title or path"
          placeholder="Search title or path…"
          style={{ maxWidth: 260 }}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
          }}
        />
      </div>
      {page.error ? <Alert tone="bad">{page.error}</Alert> : null}
      {page.loading && !page.data ? <Loading /> : null}
      {page.data && rows.length === 0 ? (
        <Empty title={terminal ? "No findings match" : "No findings yet"}>
          <p className="small">
            {terminal
              ? "No enabled rule reported an issue for these filters. Check coverage: excluded or failed files were not reviewed."
              : "Results appear as each engine finishes."}
          </p>
        </Empty>
      ) : null}
      {rows.length > 0 ? (
        <div className="table-wrap">
          <table className="data-table">
            <caption className="visually-hidden">Findings</caption>
            <thead>
              <tr>
                <th scope="col">Severity</th>
                <th scope="col">Finding</th>
                <th scope="col">Location</th>
                <th scope="col">Rule</th>
                <th scope="col">Issue</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((finding) => (
                <tr
                  key={finding.id}
                  className="clickable"
                  data-testid="finding-row"
                  onClick={() => {
                    navigate(`#/findings/${finding.id}`);
                  }}
                >
                  <td>
                    <SeverityChip severity={finding.severity} />
                  </td>
                  <th scope="row" style={{ fontWeight: 600 }}>
                    <a href={`#/findings/${finding.id}`}>{finding.title}</a>
                    <div className="muted small">{titleCase(finding.category)}</div>
                    {(finding.also_reported_by ?? []).length > 0 ? (
                      <div className="also" data-testid="also-reported">
                        <Icon name="branch" size={12} /> also reported by{" "}
                        {(finding.also_reported_by ?? []).map((other) => other.engine).join(", ")}
                      </div>
                    ) : null}
                  </th>
                  <td className="mono small wrap-anywhere">{findingLocation(finding)}</td>
                  <td className="small wrap-anywhere" style={{ maxWidth: 220 }}>
                    <span className="badge badge-neutral">{finding.engine}</span>{" "}
                    <span className="mono">{finding.rule_id}</span>
                  </td>
                  <td>{finding.issue ? <StatusBadge state={finding.issue.status} /> : "—"}</td>
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
          onClick={() => {
            listFindings(scanId, {
              severity,
              engine,
              category,
              issueStatus,
              q: query,
              cursor,
            }).then(
              (next) => {
                setExtra((previous) => [...previous, ...next.items]);
                setCursor(next.next_cursor);
              },
              () => {
                setCursor(null);
              },
            );
          }}
        >
          Load more
        </button>
      ) : null}
    </section>
  );
}

function CoverageTab({ scanId, terminal }: { scanId: string; terminal: boolean }) {
  const [engine, setEngine] = useState("");
  const [outcome, setOutcome] = useState("");
  const page = useAsync(
    (signal) => listCoverage(scanId, { engine, outcome }, signal),
    [scanId, engine, outcome, terminal],
  );
  return (
    <section className="card stack" aria-labelledby="coverage-title">
      <div className="card-head" style={{ marginBottom: 0 }}>
        <div>
          <h2 id="coverage-title" className="card-title">
            Per-file coverage
          </h2>
          <p className="card-sub">
            Eligible files per engine and what actually happened to each. Failed and not-attempted
            files were not reviewed.
          </p>
        </div>
      </div>
      <div className="row">
        <select
          aria-label="Coverage engine"
          value={engine}
          style={{ width: "auto" }}
          onChange={(event) => {
            setEngine(event.target.value);
          }}
        >
          <option value="">All engines</option>
          {ENGINES.map((name) => (
            <option key={name} value={name}>
              {ENGINE_LABELS[name]}
            </option>
          ))}
        </select>
        <select
          aria-label="Coverage outcome"
          value={outcome}
          style={{ width: "auto" }}
          onChange={(event) => {
            setOutcome(event.target.value);
          }}
        >
          <option value="">All outcomes</option>
          <option value="ANALYZED">Analyzed</option>
          <option value="FAILED">Failed</option>
          <option value="NOT_ATTEMPTED">Not attempted</option>
        </select>
      </div>
      {page.error ? <Alert tone="bad">{page.error}</Alert> : null}
      {page.loading && !page.data ? <Loading /> : null}
      {page.data?.items.length === 0 ? <Empty title="No coverage rows" /> : null}
      {page.data && page.data.items.length > 0 ? (
        <div className="table-wrap" style={{ maxHeight: 560 }}>
          <table className="data-table">
            <caption className="visually-hidden">Per-file engine coverage</caption>
            <thead>
              <tr>
                <th scope="col">File</th>
                <th scope="col">Engine</th>
                <th scope="col">Outcome</th>
                <th scope="col">Reason</th>
              </tr>
            </thead>
            <tbody>
              {page.data.items.map((row) => (
                <tr key={`${row.file_id}-${row.engine}`} data-testid="coverage-row">
                  <th scope="row" className="mono small" style={{ fontWeight: 500 }}>
                    {row.path}
                  </th>
                  <td>{row.engine}</td>
                  <td>
                    <StatusBadge
                      state={
                        row.outcome === "ANALYZED"
                          ? "SUCCEEDED"
                          : row.outcome === "FAILED"
                            ? "FAILED"
                            : "CANCELED"
                      }
                      label={titleCase(row.outcome)}
                    />
                  </td>
                  <td className="small secondary">
                    {row.cached ? (
                      <span className="badge badge-neutral" style={{ marginRight: 6 }}>
                        cached
                      </span>
                    ) : null}
                    {row.cached ? "" : (row.reason ?? "—")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

const GROUPS: { key: keyof GroupSet; label: string; hint: string; state: string }[] = [
  { key: "new", label: "New", hint: "Reported now, not before", state: "OPEN" },
  {
    key: "unchanged",
    label: "Still present",
    hint: "Reported in both scans",
    state: "VERIFIED_PRESENT",
  },
  {
    key: "verified_absent",
    label: "Verified absent",
    hint: "Compatible engine analyzed the file and did not report it",
    state: "VERIFIED_ABSENT",
  },
  {
    key: "not_rechecked",
    label: "Not rechecked",
    hint: "Engine failed or the file was not analyzed",
    state: "NOT_RECHECKED",
  },
  {
    key: "unknown",
    label: "Unknown",
    hint: "File deleted/renamed, or engine version or rules changed",
    state: "UNKNOWN",
  },
  {
    key: "rule_obsolete",
    label: "Rule obsolete",
    hint: "Rule no longer enabled",
    state: "RULE_OBSOLETE",
  },
];

interface GroupSet {
  new: ComparisonGroup;
  unchanged: ComparisonGroup;
  verified_absent: ComparisonGroup;
  not_rechecked: ComparisonGroup;
  unknown: ComparisonGroup;
  rule_obsolete: ComparisonGroup;
}

function CompareTab({ scan }: { scan: Scan }) {
  const scans = useAsync((signal) => listScans(scan.project_id, signal), [scan.project_id]);
  const candidates = (scans.data ?? []).filter(
    (other) => other.id !== scan.id && isTerminalScan(other.state),
  );
  const [base, setBase] = useState("");
  const [group, setGroup] = useState<keyof GroupSet>("new");
  const baseId =
    base ||
    candidates.find((other) => other.created_at < scan.created_at)?.id ||
    candidates[0]?.id ||
    "";
  const comparison = useAsync(
    (signal) => (baseId ? compareScans(scan.id, baseId, signal) : Promise.resolve(null)),
    [scan.id, baseId],
  );
  if (scans.error) return <Alert tone="bad">{scans.error}</Alert>;
  if (!scans.data) return <Loading />;
  if (candidates.length === 0) {
    return (
      <Empty title="Nothing to compare with yet">
        <p className="small">Run another scan of this project (any snapshot) to compare results.</p>
      </Empty>
    );
  }
  const data = comparison.data;
  const selected = data ? data[group] : null;
  return (
    <section className="card stack" aria-labelledby="compare-title">
      <div className="card-head" style={{ marginBottom: 0 }}>
        <div>
          <h2 id="compare-title" className="card-title">
            Compare with an earlier scan
          </h2>
          <p className="card-sub">
            Absence counts as fixed only when a compatible, completed engine analyzed the file.
          </p>
        </div>
        <select
          aria-label="Base scan"
          value={baseId}
          style={{ width: "auto" }}
          onChange={(event) => {
            setBase(event.target.value);
          }}
        >
          {candidates.map((other) => (
            <option key={other.id} value={other.id}>
              {formatRelative(other.created_at)} · {titleCase(other.state)} ·{" "}
              {shortHash(other.manifest_sha256, 8)}
            </option>
          ))}
        </select>
      </div>
      {comparison.error ? <Alert tone="bad">{comparison.error}</Alert> : null}
      {!data ? <Loading /> : null}
      {data ? (
        <>
          <div className="tiles" role="group" aria-label="Comparison groups">
            {GROUPS.map((item) => (
              <button
                key={item.key}
                type="button"
                className="tile"
                aria-pressed={group === item.key}
                data-testid={`compare-${item.key}`}
                onClick={() => {
                  setGroup(item.key);
                }}
              >
                <StatusBadge state={item.state} label={item.label} />
                <span className="tile-value">{data[item.key].count}</span>
                <span className="tile-label">{item.hint}</span>
              </button>
            ))}
          </div>
          {selected && selected.items.length > 0 ? (
            <div className="table-wrap" style={{ maxHeight: 420 }}>
              <table className="data-table">
                <caption className="visually-hidden">
                  {GROUPS.find((g) => g.key === group)?.label} findings
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Severity</th>
                    <th scope="col">Finding</th>
                    <th scope="col">Engine</th>
                    <th scope="col">Why</th>
                  </tr>
                </thead>
                <tbody>
                  {selected.items.map((item) => (
                    <tr key={item.fingerprint}>
                      <td>
                        <SeverityChip severity={item.severity} />
                      </td>
                      <th scope="row" style={{ fontWeight: 600 }}>
                        <a href={`#/findings/${item.finding_id ?? item.base_finding_id ?? ""}`}>
                          {item.title}
                        </a>
                        <div className="mono small muted">{item.path}</div>
                      </th>
                      <td className="small">
                        <span className="badge badge-neutral">{item.engine}</span>
                      </td>
                      <td className="small secondary">{item.reason ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty title="No findings in this group" />
          )}
          {selected?.truncated ? (
            <p className="hint">Showing the first {selected.items.length} items.</p>
          ) : null}
          <div className="table-wrap">
            <table className="data-table">
              <caption className="visually-hidden">Engine compatibility</caption>
              <thead>
                <tr>
                  <th scope="col">Engine</th>
                  <th scope="col">Base</th>
                  <th scope="col">This scan</th>
                  <th scope="col">Compatibility</th>
                </tr>
              </thead>
              <tbody>
                {data.engines.map((engine) => (
                  <tr key={engine.engine}>
                    <th scope="row">{ENGINE_LABELS[engine.engine] ?? engine.engine}</th>
                    <td className="small">
                      {engine.base_state ? titleCase(engine.base_state) : "—"}{" "}
                      <span className="mono muted">{engine.base_version ?? ""}</span>
                    </td>
                    <td className="small">
                      {engine.target_state ? titleCase(engine.target_state) : "—"}{" "}
                      <span className="mono muted">{engine.target_version ?? ""}</span>
                    </td>
                    <td className="small">
                      <StatusBadge
                        state={engine.compatible ? "SUCCEEDED" : "PARTIAL"}
                        label={engine.compatible ? "Compatible" : "Limited"}
                      />{" "}
                      <span className="secondary">{engine.note}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ul className="small secondary" style={{ margin: 0, paddingLeft: 18 }}>
            {data.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}

export function ScanPage({ scanId, tab }: { scanId: string; tab: string }) {
  const [terminalSeen, setTerminalSeen] = useState(false);
  const tick = useScanEvents(scanId, !terminalSeen);
  const scan = useAsync(
    (signal) =>
      fetchScan(scanId, signal).then((result) => {
        if (isTerminalScan(result.state)) setTerminalSeen(true);
        return result;
      }),
    [scanId, tick],
  );
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (scan.error && !scan.data) return <Alert tone="bad">{scan.error}</Alert>;
  if (!scan.data) return <Loading />;
  const data = scan.data;
  const terminal = isTerminalScan(data.state);
  const limitations = Array.isArray(data.summary?.limitations)
    ? (data.summary.limitations as string[])
    : [];
  const byCategory = (data.summary?.by_category ?? {}) as Record<string, number>;
  const base = `#/scans/${scanId}`;

  return (
    <>
      <PageHeader
        eyebrow={
          <>
            <a href={`#/projects/${data.project_id}`}>Project</a> <span aria-hidden="true">/</span>
            <a href={`#/snapshots/${data.snapshot_id}`}>
              Snapshot {shortHash(data.manifest_sha256, 8)}
            </a>
            <span aria-hidden="true">/</span> Scan
          </>
        }
        title={
          <span className="row">
            Baseline scan <StatusBadge state={data.state} />
          </span>
        }
        sub={`Started ${formatRelative(data.started_at ?? data.created_at)} · policy ${data.policy_version} · ${data.cache_mode === "refresh" ? "full rescan" : "cache reuse on"} · source-only analysis (no build or runtime verification)`}
        actions={
          terminal ? (
            <>
              <a
                className="btn btn-ghost"
                href={exportUrl(scanId, "sarif")}
                download
                data-testid="export-sarif"
              >
                <Icon name="file" size={16} /> SARIF
              </a>
              <a
                className="btn btn-ghost"
                href={exportUrl(scanId, "json")}
                download
                data-testid="export-json"
              >
                <Icon name="file" size={16} /> JSON
              </a>
              {(["use", "refresh"] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  className={mode === "use" ? "btn btn-primary" : "btn btn-ghost"}
                  disabled={busy}
                  title={
                    mode === "use"
                      ? "Reuse compatible per-file results for unchanged files"
                      : "Ignore cached results and analyze every file again"
                  }
                  onClick={() => {
                    setBusy(true);
                    startScan(data.project_id, data.snapshot_id, mode).then(
                      (next) => {
                        navigate(`#/scans/${next.id}`);
                        setBusy(false);
                      },
                      (caught: unknown) => {
                        setActionError(describeError(caught));
                        setBusy(false);
                      },
                    );
                  }}
                >
                  <Icon name="scan" size={16} /> {mode === "use" ? "Re-run" : "Full rescan"}
                </button>
              ))}
            </>
          ) : (
            <button
              type="button"
              className="btn btn-danger"
              disabled={busy || data.cancel_requested_at !== null}
              onClick={() => {
                setBusy(true);
                cancelScan(scanId).then(
                  () => {
                    setBusy(false);
                    scan.reload();
                  },
                  (caught: unknown) => {
                    setActionError(describeError(caught));
                    setBusy(false);
                  },
                );
              }}
            >
              <Icon name="x" size={16} /> {data.cancel_requested_at ? "Cancelling…" : "Cancel scan"}
            </button>
          )
        }
      />
      {actionError ? <Alert tone="bad">{actionError}</Alert> : null}
      <section aria-label="Pipeline progress" aria-live="polite">
        <Pipeline scan={data} />
      </section>
      <div className="grid grid-3">
        {data.engines.map((run) => (
          <EngineCard key={run.engine} run={run} />
        ))}
      </div>
      {terminal ? (
        <div className="grid grid-2">
          <section className="card" aria-labelledby="sev-title">
            <h2 id="sev-title" className="card-title" style={{ marginBottom: 12 }}>
              Findings by severity
            </h2>
            <SeverityBars counts={severityCounts(data.summary?.by_severity)} />
          </section>
          <section className="card" aria-labelledby="cat-title">
            <h2 id="cat-title" className="card-title" style={{ marginBottom: 12 }}>
              Findings by category
            </h2>
            <CategoryBars counts={byCategory} />
          </section>
        </div>
      ) : null}
      {limitations.length > 0 ? (
        <Alert tone={data.state === "SUCCEEDED" ? "info" : "warn"}>
          <p>
            <strong>Limitations of this result</strong>
          </p>
          <ul
            className="small"
            style={{ margin: "6px 0 0", paddingLeft: 18 }}
            data-testid="limitations"
          >
            {limitations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </Alert>
      ) : null}
      <Tabs
        current={tab}
        items={[
          { id: "findings", label: "Findings", href: base },
          { id: "coverage", label: "Coverage", href: `${base}?tab=coverage` },
          { id: "compare", label: "Compare", href: `${base}?tab=compare` },
        ]}
      />
      {tab === "coverage" ? <CoverageTab scanId={scanId} terminal={terminal} /> : null}
      {tab === "compare" ? (
        terminal ? (
          <CompareTab scan={data} />
        ) : (
          <Empty title="Comparison is available when the scan finishes" />
        )
      ) : null}
      {tab !== "coverage" && tab !== "compare" ? (
        <FindingsTab scanId={scanId} terminal={terminal} />
      ) : null}
    </>
  );
}
