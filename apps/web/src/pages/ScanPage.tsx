import { useEffect, useState } from "react";

import {
  describeError,
  type ComparisonItem,
  type EngineRun,
  type Finding,
  type Scan,
} from "../api/client";
import {
  cancelScan,
  compareScans,
  exportUrl,
  fetchProject,
  fetchScan,
  fetchSnapshot,
  listCoverage,
  listFindings,
  listScans,
  startScan,
} from "../api/endpoints";
import { CategoryBars, CoverageMeter } from "../components/Charts";
import { Alert, Disclosure, Empty, Loading, PageHeader, Tabs } from "../components/Common";
import { FileLocation } from "../components/FileLocation";
import { Icon } from "../components/Icon";
import {
  SEVERITIES,
  SeverityBars,
  SeverityChip,
  SeverityMark,
  severityCounts,
} from "../components/Severity";
import { StatusBadge, StatusIcon, findingTotal, isTerminalScan } from "../components/Status";
import { formatDuration, formatRelative, titleCase } from "../lib/format";
import {
  CATEGORY_LABELS,
  CHECK_ORDER,
  CHECKS,
  FINDING_CHECKS,
  categoryLabel,
  checkName,
  plural,
} from "../lib/labels";
import { reviewLabel, reviewNumbers } from "../lib/reviews";
import { navigate } from "../lib/router";
import { useAsync } from "../lib/useAsync";

const ISSUE_STATUSES = ["OPEN", "TRIAGED", "ACCEPTED_RISK", "FALSE_POSITIVE", "RESOLVED"];

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

function stepMeta(run: EngineRun | undefined, terminal: boolean): string {
  if (!run) return terminal ? "Not needed" : "Waiting";
  if (run.state === "RUNNING") return "Working…";
  if (run.state === "QUEUED") return "Waiting";
  if (run.state === "NOT_APPLICABLE") return "No matching files";
  if (run.state === "UNAVAILABLE") return "Not available";
  return plural(run.files_succeeded, "file");
}

function Progress({ scan }: { scan: Scan }) {
  const runs = new Map(scan.engines.map((run) => [run.engine, run]));
  const terminal = isTerminalScan(scan.state);
  const stages = [
    { id: "snapshot", name: "Code received", state: "SUCCEEDED", meta: "Ready" },
    ...CHECK_ORDER.map((engine) => {
      const run = runs.get(engine);
      return {
        id: engine,
        name: checkName(engine),
        state: run?.state ?? (terminal ? "NOT_APPLICABLE" : "QUEUED"),
        meta: stepMeta(run, terminal),
      };
    }),
    {
      id: "publish",
      name: "Results",
      state: terminal ? scan.state : "QUEUED",
      meta: terminal ? plural(findingTotal(scan.summary) ?? 0, "finding") : "Pending",
    },
  ];
  const checks = scan.engines.length;
  const finished = scan.engines.filter((run) => !["QUEUED", "RUNNING"].includes(run.state)).length;
  const summary = terminal
    ? `Finished · ${plural(findingTotal(scan.summary) ?? 0, "finding")}`
    : `Checking your code · ${String(finished)} of ${String(checks || CHECK_ORDER.length)} checks done`;
  return (
    <section className="card" aria-labelledby="progress-title">
      <div className="card-head">
        <div>
          <h2 id="progress-title" className="card-title">
            Progress
          </h2>
          <p className="card-sub">{summary}</p>
        </div>
        {terminal ? null : <span className="pulse-dot" aria-hidden="true" />}
      </div>
      <ol className="steps" aria-label="Review steps">
        {stages.map((stage) => (
          <li
            key={stage.id}
            className="step"
            data-state={stage.state}
            data-testid={`stage-${stage.id}`}
          >
            <StatusIcon state={stage.state} />
            <span className="step-text">
              <span className="step-name">{stage.name}</span>
              <span className="step-meta">{stage.meta}</span>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function CheckTechnicalDetails({ run }: { run: EngineRun }) {
  const cache = run.diagnostics?.cache as { eligible?: boolean } | undefined;
  return (
    <dl className="details" data-testid={`engine-${run.engine}`}>
      <div>
        <dt>Tool</dt>
        <dd className="mono">
          {CHECKS[run.engine]?.tool ?? run.engine} {run.engine_version ?? ""}
        </dd>
      </div>
      <div>
        <dt>Rule set</dt>
        <dd className="mono">{run.ruleset_id ?? "—"}</dd>
      </div>
      <div>
        <dt>Rules</dt>
        <dd>
          {run.enabled_rule_count ?? (run.engine === "trivy" ? "vulnerability DB + secrets" : "—")}
        </dd>
      </div>
      <div>
        <dt>Duration</dt>
        <dd>{formatDuration(run.duration_ms)}</dd>
      </div>
      <div>
        <dt>Saved results reused</dt>
        <dd data-testid={`cache-${run.engine}`}>
          {run.cache_hits || run.cache_misses
            ? `${String(run.cache_hits)} reused · ${String(run.cache_misses)} run`
            : cache?.eligible === false
              ? "not reusable"
              : "—"}
        </dd>
      </div>
      {typeof run.diagnostics?.db_updated_at === "string" ? (
        <div>
          <dt>Vulnerability data</dt>
          <dd>
            Vulnerability DB updated {formatRelative(run.diagnostics.db_updated_at)}
            {run.diagnostics.db_stale === true ? " (older than a day)" : ""}
          </dd>
        </div>
      ) : null}
      {run.error_code ? (
        <div>
          <dt>Error code</dt>
          <dd className="mono">{run.error_code}</dd>
        </div>
      ) : null}
    </dl>
  );
}

function ChecksPanel({ scan, limitations }: { scan: Scan; limitations: string[] }) {
  const runs = [...scan.engines].sort(
    (a, b) => CHECK_ORDER.indexOf(a.engine) - CHECK_ORDER.indexOf(b.engine),
  );
  const problems = runs.filter((run) => ["FAILED", "UNAVAILABLE", "PARTIAL"].includes(run.state));
  return (
    <Disclosure
      testId="checks-panel"
      defaultOpen={problems.length > 0}
      summary={
        <>
          What was checked
          <span className="muted small">
            {" "}
            · {plural(runs.length, "check")}
            {problems.length ? ` · ${plural(problems.length, "problem")}` : ""}
          </span>
        </>
      }
    >
      <ul className="check-list">
        {runs.map((run) => (
          <li key={run.engine} className="check-row" data-testid={`check-${run.engine}`}>
            <div className="check-main">
              <div className="row row-between">
                <strong>{checkName(run.engine)}</strong>
                <StatusBadge state={run.state} />
              </div>
              <p className="small muted">{CHECKS[run.engine]?.description}</p>
              {run.files_eligible > 0 ? <CoverageMeter run={run} /> : null}
              {run.error_message ? <p className="check-error">{run.error_message}</p> : null}
            </div>
          </li>
        ))}
      </ul>
      {limitations.length > 0 ? (
        <div className="stack stack-xs">
          <strong className="small">Keep in mind</strong>
          <ul className="list small secondary" data-testid="limitations">
            {limitations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <Disclosure summary="Technical details" testId="checks-technical">
        <div className="grid grid-3">
          {runs.map((run) => (
            <section key={run.engine} className="stack stack-sm">
              <strong className="small">{checkName(run.engine)}</strong>
              <CheckTechnicalDetails run={run} />
            </section>
          ))}
        </div>
        <p className="hint">
          Review policy {scan.policy_version} ·{" "}
          {scan.cache_mode === "refresh" ? "full review" : "unchanged files reuse saved results"} ·
          source code only (the application is not built or run).
        </p>
      </Disclosure>
    </Disclosure>
  );
}

/** Findings that several checks reported at the same place are shown once. */
function mergeCorrelated(rows: Finding[]): { rows: Finding[]; merged: number } {
  const hidden = new Set<string>();
  const kept: Finding[] = [];
  for (const finding of rows) {
    if (hidden.has(finding.id)) continue;
    kept.push(finding);
    for (const other of finding.also_reported_by ?? []) hidden.add(other.id);
  }
  return { rows: kept, merged: rows.length - kept.length };
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
  const { rows, merged } = mergeCorrelated([...(page.data?.items ?? []), ...extra]);

  return (
    <section className="card stack" aria-labelledby="findings-title">
      <div className="card-head">
        <div>
          <h2 id="findings-title" className="card-title">
            Findings
          </h2>
          <p className="card-sub">Most severe first. Open a finding to see the code and the fix.</p>
        </div>
        <span className="muted small" aria-live="polite">
          {page.data ? plural(page.data.total, "finding") : ""}
          {merged ? ` · ${String(merged)} duplicate${merged === 1 ? "" : "s"} merged` : ""}
        </span>
      </div>
      <div className="filters" role="group" aria-label="Finding filters">
        <div className="row">
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
        </div>
        <div className="row">
          <select
            aria-label="Type"
            value={category}
            onChange={(event) => {
              setCategory(event.target.value);
            }}
          >
            <option value="">All types</option>
            {Object.keys(CATEGORY_LABELS).map((c) => (
              <option key={c} value={c}>
                {categoryLabel(c)}
              </option>
            ))}
          </select>
          <select
            aria-label="Check"
            value={engine}
            onChange={(event) => {
              setEngine(event.target.value);
            }}
          >
            <option value="">All checks</option>
            {FINDING_CHECKS.map((name) => (
              <option key={name} value={name}>
                {checkName(name)}
              </option>
            ))}
          </select>
          <select
            aria-label="Issue status"
            value={issueStatus}
            onChange={(event) => {
              setIssueStatus(event.target.value);
            }}
          >
            <option value="">Any status</option>
            {ISSUE_STATUSES.map((value) => (
              <option key={value} value={value}>
                {titleCase(value)}
              </option>
            ))}
          </select>
          <input
            type="search"
            aria-label="Search findings by title or path"
            placeholder="Search title or file…"
            className="search"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
            }}
          />
        </div>
      </div>
      {page.error ? <Alert tone="bad">{page.error}</Alert> : null}
      {page.loading && !page.data ? <Loading /> : null}
      {page.data && rows.length === 0 ? (
        <Empty title={terminal ? "No findings match" : "No findings yet"}>
          <p className="small">
            {terminal
              ? "Nothing matches these filters. Files that could not be checked are listed under Files checked."
              : "Findings appear as each check finishes."}
          </p>
        </Empty>
      ) : null}
      {rows.length > 0 ? (
        <div className="table-wrap">
          <table className="data-table findings-table">
            <caption className="visually-hidden">Findings</caption>
            <thead>
              <tr>
                <th scope="col">Severity</th>
                <th scope="col">Finding</th>
                <th scope="col">Where</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((finding) => {
                const others = finding.also_reported_by ?? [];
                return (
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
                    <th scope="row">
                      <a href={`#/findings/${finding.id}`}>{finding.title}</a>
                      <div className="muted small">
                        {categoryLabel(finding.category)} · {checkName(finding.engine)}
                      </div>
                      {others.length > 0 ? (
                        <div className="also" data-testid="also-reported">
                          <Icon name="check" size={12} /> Also found by{" "}
                          {[...new Set(others.map((other) => checkName(other.engine)))].join(", ")}
                        </div>
                      ) : null}
                    </th>
                    <td className="location">
                      <FileLocation
                        path={finding.path}
                        line={finding.start_line}
                        endLine={finding.end_line}
                        {...(finding.start_line === null
                          ? {
                              note:
                                finding.anchor_kind === "dependency" ? "dependency" : "whole file",
                            }
                          : {})}
                      />
                    </td>
                    <td>{finding.issue ? <StatusBadge state={finding.issue.status} /> : "—"}</td>
                  </tr>
                );
              })}
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
          Show more
        </button>
      ) : null}
    </section>
  );
}

const OUTCOMES: Record<string, { state: string; label: string }> = {
  ANALYZED: { state: "SUCCEEDED", label: "Checked" },
  FAILED: { state: "FAILED", label: "Could not read" },
  NOT_ATTEMPTED: { state: "CANCELED", label: "Not checked" },
};

function CoverageTab({ scanId, terminal }: { scanId: string; terminal: boolean }) {
  const [engine, setEngine] = useState("");
  const [outcome, setOutcome] = useState("");
  const page = useAsync(
    (signal) => listCoverage(scanId, { engine, outcome }, signal),
    [scanId, engine, outcome, terminal],
  );
  return (
    <section className="card stack" aria-labelledby="coverage-title">
      <div className="card-head">
        <div>
          <h2 id="coverage-title" className="card-title">
            Files checked
          </h2>
          <p className="card-sub">
            Each file and what every check did with it. Files that could not be read or were not
            checked may still contain problems.
          </p>
        </div>
      </div>
      <div className="row">
        <select
          aria-label="Coverage check"
          value={engine}
          onChange={(event) => {
            setEngine(event.target.value);
          }}
        >
          <option value="">All checks</option>
          {CHECK_ORDER.map((name) => (
            <option key={name} value={name}>
              {checkName(name)}
            </option>
          ))}
        </select>
        <select
          aria-label="Coverage outcome"
          value={outcome}
          onChange={(event) => {
            setOutcome(event.target.value);
          }}
        >
          <option value="">All results</option>
          {Object.entries(OUTCOMES).map(([value, info]) => (
            <option key={value} value={value}>
              {info.label}
            </option>
          ))}
        </select>
      </div>
      {page.error ? <Alert tone="bad">{page.error}</Alert> : null}
      {page.loading && !page.data ? <Loading /> : null}
      {page.data?.items.length === 0 ? <Empty title="No files match" /> : null}
      {page.data && page.data.items.length > 0 ? (
        <div className="table-wrap table-scroll">
          <table className="data-table">
            <caption className="visually-hidden">Files and checks</caption>
            <thead>
              <tr>
                <th scope="col">File</th>
                <th scope="col">Check</th>
                <th scope="col">Result</th>
                <th scope="col">Note</th>
              </tr>
            </thead>
            <tbody>
              {page.data.items.map((row) => {
                const info = OUTCOMES[row.outcome] ?? { state: "CANCELED", label: row.outcome };
                return (
                  <tr key={`${row.file_id}-${row.engine}`} data-testid="coverage-row">
                    <th scope="row">
                      <FileLocation path={row.path} />
                    </th>
                    <td>{checkName(row.engine)}</td>
                    <td>
                      <StatusBadge state={info.state} label={info.label} />
                    </td>
                    <td className="small secondary">
                      {row.cached ? "Unchanged since an earlier review" : (row.reason ?? "—")}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

type GroupKey = "new" | "verified_absent" | "unchanged" | "unverified";

const GROUPS: { key: GroupKey; label: string; hint: string; state: string }[] = [
  { key: "new", label: "New", hint: "Found now, not before", state: "OPEN" },
  {
    key: "verified_absent",
    label: "Fixed",
    hint: "Checked again and gone",
    state: "VERIFIED_ABSENT",
  },
  {
    key: "unchanged",
    label: "Still present",
    hint: "Found in both reviews",
    state: "VERIFIED_PRESENT",
  },
  {
    key: "unverified",
    label: "Couldn't verify",
    hint: "File changed, moved or not checked again",
    state: "UNKNOWN",
  },
];

function CompareTab({ scan }: { scan: Scan }) {
  const scans = useAsync((signal) => listScans(scan.project_id, signal), [scan.project_id]);
  const candidates = (scans.data ?? []).filter(
    (other) => other.id !== scan.id && isTerminalScan(other.state),
  );
  const numbers = reviewNumbers(scans.data ?? []);
  const [base, setBase] = useState("");
  const [group, setGroup] = useState<GroupKey>("new");
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
      <section className="card">
        <Empty title="Nothing to compare with yet">
          <p className="small">Review this project again later to see what changed.</p>
        </Empty>
      </section>
    );
  }
  const data = comparison.data;
  const unverified: ComparisonItem[] = data
    ? [...data.not_rechecked.items, ...data.unknown.items, ...data.rule_obsolete.items]
    : [];
  const counts: Record<GroupKey, number> = data
    ? {
        new: data.new.count,
        verified_absent: data.verified_absent.count,
        unchanged: data.unchanged.count,
        unverified: data.not_rechecked.count + data.unknown.count + data.rule_obsolete.count,
      }
    : { new: 0, verified_absent: 0, unchanged: 0, unverified: 0 };
  const items: ComparisonItem[] = data
    ? group === "unverified"
      ? unverified
      : data[group].items
    : [];
  return (
    <section className="card stack" aria-labelledby="compare-title">
      <div className="card-head">
        <div>
          <h2 id="compare-title" className="card-title">
            Changes since an earlier review
          </h2>
          <p className="card-sub">
            A finding counts as fixed only when its file was checked again and the problem is gone.
          </p>
        </div>
        <select
          aria-label="Compare with"
          value={baseId}
          onChange={(event) => {
            setBase(event.target.value);
          }}
        >
          {candidates.map((other) => (
            <option key={other.id} value={other.id}>
              {reviewLabel(numbers, other.id)} · {formatRelative(other.created_at)}
            </option>
          ))}
        </select>
      </div>
      {comparison.error ? <Alert tone="bad">{comparison.error}</Alert> : null}
      {!data ? <Loading /> : null}
      {data ? (
        <>
          <div className="tiles tiles-4" role="group" aria-label="Changes">
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
                <span className="tile-value">{counts[item.key]}</span>
                <span className="tile-label">{item.hint}</span>
              </button>
            ))}
          </div>
          {items.length > 0 ? (
            <div className="table-wrap table-scroll">
              <table className="data-table">
                <caption className="visually-hidden">
                  {GROUPS.find((g) => g.key === group)?.label} findings
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Severity</th>
                    <th scope="col">Finding</th>
                    <th scope="col">Why</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.fingerprint}>
                      <td>
                        <SeverityChip severity={item.severity} />
                      </td>
                      <th scope="row">
                        <a href={`#/findings/${item.finding_id ?? item.base_finding_id ?? ""}`}>
                          {item.title}
                        </a>
                        <div className="mono small muted">{item.path}</div>
                      </th>
                      <td className="small secondary">{item.reason ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty title="Nothing in this group" />
          )}
          <Disclosure>
            <div className="table-wrap">
              <table className="data-table">
                <caption className="visually-hidden">Check compatibility</caption>
                <thead>
                  <tr>
                    <th scope="col">Check</th>
                    <th scope="col">Earlier</th>
                    <th scope="col">This review</th>
                    <th scope="col">Comparable</th>
                  </tr>
                </thead>
                <tbody>
                  {data.engines.map((engine) => (
                    <tr key={engine.engine}>
                      <th scope="row">{checkName(engine.engine)}</th>
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
                          label={engine.compatible ? "Yes" : "Limited"}
                        />{" "}
                        <span className="secondary">{engine.note}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <ul className="list small secondary">
              {data.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </Disclosure>
        </>
      ) : null}
    </section>
  );
}

function tookLabel(scan: Scan): string | null {
  if (!scan.started_at || !scan.finished_at) return null;
  const ms = new Date(scan.finished_at).getTime() - new Date(scan.started_at).getTime();
  if (ms < 60_000) return `took ${String(Math.max(1, Math.round(ms / 1000)))} s`;
  return `took ${String(Math.round(ms / 60_000))} min`;
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
  const projectId = scan.data?.project_id ?? null;
  const snapshotId = scan.data?.snapshot_id ?? null;
  const project = useAsync(
    (signal) => (projectId ? fetchProject(projectId, signal) : Promise.resolve(null)),
    [projectId],
  );
  const snapshot = useAsync(
    (signal) => (snapshotId ? fetchSnapshot(snapshotId, signal) : Promise.resolve(null)),
    [snapshotId],
  );
  const siblings = useAsync(
    (signal) => (projectId ? listScans(projectId, signal) : Promise.resolve([])),
    [projectId, terminalSeen],
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
  const label = reviewLabel(reviewNumbers(siblings.data ?? []), scanId);
  const took = tookLabel(data);
  const reviewAgain = (mode: "use" | "refresh") => {
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
  };

  return (
    <>
      <PageHeader
        eyebrow={
          <>
            <a href={`#/projects/${data.project_id}`} data-testid="project-link">
              {project.data?.name ?? "Project"}
            </a>
            <span aria-hidden="true">/</span>
            <a href={`#/snapshots/${data.snapshot_id}`} data-testid="upload-link">
              {snapshot.data?.source_name ?? "Upload"}
            </a>
            <span aria-hidden="true">/</span> {label}
          </>
        }
        title={
          <span className="row">
            Review results <StatusBadge state={data.state} />
          </span>
        }
        sub={`Started ${formatRelative(data.started_at ?? data.created_at)}${took ? ` · ${took}` : ""}`}
        actions={
          terminal ? (
            <>
              <div className="btn-group" role="group" aria-label="Download report">
                <a
                  className="btn btn-ghost"
                  href={exportUrl(scanId, "json")}
                  download
                  data-testid="export-json"
                  title="Full report as JSON"
                >
                  <Icon name="download" size={16} /> Report
                </a>
                <a
                  className="btn btn-ghost"
                  href={exportUrl(scanId, "sarif")}
                  download
                  data-testid="export-sarif"
                  title="SARIF 2.1.0 for code-scanning tools"
                >
                  SARIF
                </a>
              </div>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={busy}
                title="Check every file again, ignoring results saved from earlier reviews"
                onClick={() => {
                  reviewAgain("refresh");
                }}
              >
                Full review
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy}
                title="Review the same upload again; unchanged files reuse earlier results"
                onClick={() => {
                  reviewAgain("use");
                }}
              >
                <Icon name="scan" size={16} /> Review again
              </button>
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
              <Icon name="x" size={16} /> {data.cancel_requested_at ? "Stopping…" : "Stop review"}
            </button>
          )
        }
      />
      {actionError ? <Alert tone="bad">{actionError}</Alert> : null}
      <div aria-live="polite">
        <Progress scan={data} />
      </div>
      {terminal && data.state !== "SUCCEEDED" && limitations.length > 0 ? (
        <Alert tone="warn">
          <p>
            <strong>Some files could not be checked.</strong> The findings below are still valid,
            but problems in those files may be missing. Details are under “What was checked”.
          </p>
        </Alert>
      ) : null}
      {terminal ? (
        <div className="grid grid-2">
          <section className="card" aria-labelledby="sev-title">
            <h2 id="sev-title" className="card-title">
              By severity
            </h2>
            <SeverityBars counts={severityCounts(data.summary?.by_severity)} />
          </section>
          <section className="card" aria-labelledby="cat-title">
            <h2 id="cat-title" className="card-title">
              By type
            </h2>
            <CategoryBars counts={byCategory} />
          </section>
        </div>
      ) : null}
      <ChecksPanel scan={data} limitations={limitations} />
      <Tabs
        current={tab}
        items={[
          { id: "findings", label: "Findings", href: base },
          { id: "coverage", label: "Files checked", href: `${base}?tab=coverage` },
          { id: "compare", label: "Changes", href: `${base}?tab=compare` },
        ]}
      />
      {tab === "coverage" ? <CoverageTab scanId={scanId} terminal={terminal} /> : null}
      {tab === "compare" ? (
        terminal ? (
          <CompareTab scan={data} />
        ) : (
          <section className="card">
            <Empty title="Changes appear when the review finishes" />
          </section>
        )
      ) : null}
      {tab !== "coverage" && tab !== "compare" ? (
        <FindingsTab scanId={scanId} terminal={terminal} />
      ) : null}
    </>
  );
}
