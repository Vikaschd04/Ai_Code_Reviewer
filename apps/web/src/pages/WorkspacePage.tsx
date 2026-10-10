/** Fix workspace (P08): fix many issues by hand or automatically, re-check the changes on a copy
 * of the upload, compare versions and download a patch or the changed files. The upload itself
 * is never changed. Loaded lazily (it carries the code editor). */
import { useEffect, useMemo, useState } from "react";

import {
  ApiError,
  describeError,
  type AiFixCandidate,
  type AiRun,
  type Workspace,
  type WorkspaceCheck,
  type WorkspaceFileContent,
  type WorkspaceFixResult,
  type WorkspaceIssue,
  type WorkspaceIssuePage,
} from "../api/client";
import {
  applyAiFix,
  applyWorkspaceFixes,
  cancelAiRun,
  cancelWorkspaceCheck,
  deleteWorkspaceFile,
  fetchWorkspace,
  fetchWorkspaceFile,
  listAiFixes,
  listFiles,
  listWorkspaceIssues,
  openWorkspacePullRequest,
  requestAiFix,
  revertWorkspaceFile,
  saveWorkspaceFile,
  startWorkspaceCheck,
  workspaceExportUrl,
  type WorkspaceIssueQuery,
} from "../api/endpoints";
import { CodeEditor, CompareView } from "../components/CodeEditor";
import { Alert, Disclosure, Empty, Loading, PageHeader, Tabs } from "../components/Common";
import { FileLocation } from "../components/FileLocation";
import { DiffView } from "../components/Fixes";
import { Icon } from "../components/Icon";
import { SeverityChip } from "../components/Severity";
import { StatusBadge } from "../components/Status";
import { formatDate, formatRelative, shortHash } from "../lib/format";
import { CHANGE_ACTIONS, CHANGE_SOURCES, checkName, editFlag, plural } from "../lib/labels";
import { navigate, workspaceHref } from "../lib/router";
import { useAsync } from "../lib/useAsync";

type SetWorkspace = (next: Workspace) => void;

function checkActive(check: WorkspaceCheck | null | undefined): boolean {
  return check?.state === "QUEUED" || check?.state === "RUNNING";
}

function count(check: WorkspaceCheck | null | undefined, key: string): number {
  const counts = check?.result?.counts;
  if (!counts || typeof counts !== "object") return 0;
  const value = (counts as Record<string, unknown>)[key];
  return typeof value === "number" ? value : 0;
}

interface NewItem {
  title: string;
  path: string;
  line: number | null;
  severity: string;
}

function newItems(check: WorkspaceCheck | null | undefined): NewItem[] {
  const items = check?.result?.new_items;
  if (!Array.isArray(items)) return [];
  return items.flatMap((item: unknown) => {
    if (!item || typeof item !== "object") return [];
    const row = item as Record<string, unknown>;
    return [
      {
        title: typeof row.title === "string" ? row.title : "Problem",
        path: typeof row.path === "string" ? row.path : "",
        line: typeof row.line === "number" ? row.line : null,
        severity: typeof row.severity === "string" ? row.severity : "info",
      },
    ];
  });
}

// -- export ----------------------------------------------------------------------------------------

function ExportMenu({ workspace }: { workspace: Workspace }) {
  const empty = workspace.files.length === 0;
  const links: { format: Parameters<typeof workspaceExportUrl>[1]; label: string; hint: string }[] =
    [
      { format: "patch", label: "Patch", hint: "One file for git apply" },
      { format: "changed", label: "Changed files", hint: "ZIP with only the changed files" },
      { format: "full", label: "Full project", hint: "ZIP of the project with your changes" },
      { format: "summary-md", label: "Summary", hint: "What changed and how it was checked" },
    ];
  return (
    <div className="btn-group" role="group" aria-label="Download your changes">
      {links.map((link) =>
        empty ? (
          <button
            key={link.format}
            type="button"
            className="btn btn-ghost btn-sm"
            disabled
            title="Make a change first"
          >
            {link.format === "patch" ? <Icon name="download" size={14} /> : null} {link.label}
          </button>
        ) : (
          <a
            key={link.format}
            className="btn btn-ghost btn-sm"
            href={workspaceExportUrl(workspace.id, link.format)}
            title={link.hint}
            download
            data-testid={`workspace-export-${link.format}`}
          >
            {link.format === "patch" ? <Icon name="download" size={14} /> : null} {link.label}
          </a>
        ),
      )}
    </div>
  );
}

// -- check -----------------------------------------------------------------------------------------

interface TypeError {
  path: string;
  line: number;
  code: string;
  message: string;
}

interface TypesResult {
  state: string;
  tool?: string | null;
  reason?: string | null;
  new_count?: number;
  new?: TypeError[];
  fixed?: number | null;
  unresolved_imports?: number;
}

function typesOf(check: WorkspaceCheck | null | undefined): TypesResult | null {
  const types = check?.result?.types;
  if (!types || typeof types !== "object" || !("state" in types)) return null;
  return types as TypesResult;
}

/** Tier 0 type-check result (P09): new and fixed TypeScript errors, or why it did not run. */
function TypesNote({
  check,
  workspaceId,
}: {
  check: WorkspaceCheck | null | undefined;
  workspaceId: string;
}) {
  const types = typesOf(check);
  if (!types || types.state === "not_applicable") return null;
  if (types.state !== "checked") {
    return (
      <p className="small muted" data-testid="workspace-types" data-state={types.state}>
        Type check: {types.reason ?? "not available."}
      </p>
    );
  }
  const added = types.new_count ?? 0;
  const fixed = types.fixed ?? 0;
  return (
    <div className="stack stack-xs" data-testid="workspace-types" data-state="checked">
      <div className="row">
        <StatusBadge
          state={added > 0 ? "FAILED" : "fixed"}
          label={added > 0 ? plural(added, "new type error") : "No new type errors"}
        />
        <span className="small muted">
          {fixed > 0 ? `${plural(fixed, "type error")} fixed · ` : ""}
          TypeScript type-check
        </span>
      </div>
      {(types.unresolved_imports ?? 0) > 0 ? (
        <p className="small muted">
          {plural(types.unresolved_imports ?? 0, "import")} of packages that are not installed could
          not be checked.
        </p>
      ) : null}
      {added > 0 ? (
        <Disclosure summary="Show the new type errors" testId="workspace-type-errors">
          <ul className="stack stack-sm plain-list">
            {(types.new ?? []).map((item, index) => (
              <li key={index} className="stack stack-xs">
                <a
                  className="small"
                  href={workspaceHref(workspaceId, "edit", item.path, item.line)}
                >
                  <FileLocation path={item.path} line={item.line} />
                </a>
                <span className="small secondary">{item.message}</span>
              </li>
            ))}
          </ul>
        </Disclosure>
      ) : null}
    </div>
  );
}

function CheckCard({ workspace, onChange }: { workspace: Workspace; onChange: SetWorkspace }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const check = workspace.latest_check;
  const active = checkActive(check);
  const finished = check && !active && check.result;
  const problems = newItems(check);
  const tiles = [
    { key: "fixed", label: "Fixed", state: "fixed" },
    { key: "still_present", label: "Still present", state: "still_present" },
    { key: "suppressed", label: "Hidden, not fixed", state: "suppressed" },
    { key: "new", label: "New problems", state: "FAILED" },
  ];
  const run = () => {
    setBusy(true);
    setError(null);
    startWorkspaceCheck(workspace.id)
      .then(() => fetchWorkspace(workspace.id))
      .then(
        (next) => {
          setBusy(false);
          onChange(next);
        },
        (caught: unknown) => {
          setError(describeError(caught));
          setBusy(false);
          void fetchWorkspace(workspace.id).then(onChange, () => undefined);
        },
      );
  };
  return (
    <section className="card stack" aria-labelledby="ws-check-title" data-testid="workspace-check">
      <div className="card-head">
        <div>
          <h2 id="ws-check-title" className="card-title">
            <Icon name="shield" size={16} /> Check my changes
          </h2>
          <p className="card-sub">
            refactorX checks a copy of your upload with your changes and compares the results with
            the original review. TypeScript is type-checked; nothing is built, run or tested, and
            Java is not compiled.
          </p>
        </div>
        {check ? <StatusBadge state={check.state} /> : null}
      </div>
      {check && !check.current && !active ? (
        <Alert tone="info">These results are for an earlier version of your changes.</Alert>
      ) : null}
      {finished ? (
        <div className="tiles tiles-4" data-testid="workspace-outcomes">
          {tiles.map((tile) => (
            <div key={tile.key} className="tile" data-outcome={tile.key}>
              <span className="tile-value">{count(check, tile.key)}</span>
              <span className="tile-label">
                <StatusBadge state={tile.state} label={tile.label} />
              </span>
            </div>
          ))}
        </div>
      ) : null}
      {finished ? <TypesNote check={check} workspaceId={workspace.id} /> : null}
      {finished && count(check, "not_rechecked") > 0 ? (
        <p className="small muted">
          {plural(count(check, "not_rechecked"), "issue")} could not be rechecked (for example a
          check did not finish for that file).
        </p>
      ) : null}
      {check?.state === "FAILED" && check.error_message ? (
        <Alert tone="bad">{check.error_message}</Alert>
      ) : null}
      {problems.length > 0 && !active ? (
        <Disclosure summary={`New problems (${String(count(check, "new"))})`} testId="ws-new">
          <ul className="stack stack-sm plain-list">
            {problems.map((item, index) => (
              <li key={index} className="row">
                <SeverityChip severity={item.severity} />
                <span className="small">{item.title}</span>
                <a
                  className="small"
                  href={workspaceHref(workspace.id, "edit", item.path, item.line)}
                >
                  <FileLocation path={item.path} line={item.line} />
                </a>
              </li>
            ))}
          </ul>
        </Disclosure>
      ) : null}
      {active && check ? (
        <div className="row">
          <span className="pulse-dot" />
          <span className="small">Checking your changes…</span>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => {
              cancelWorkspaceCheck(check.id).then(
                () => fetchWorkspace(workspace.id).then(onChange),
                (caught: unknown) => {
                  setError(describeError(caught));
                },
              );
            }}
          >
            <Icon name="x" size={14} /> Stop
          </button>
        </div>
      ) : workspace.can_edit ? (
        <div className="row">
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy || workspace.files.length === 0}
            onClick={run}
            data-testid="workspace-run-check"
          >
            <Icon name="play" size={15} />{" "}
            {check?.current ? "Check again" : busy ? "Starting…" : "Check my changes"}
          </button>
          {workspace.files.length === 0 ? (
            <span className="small muted">Make a change first.</span>
          ) : null}
        </div>
      ) : null}
      {error ? <Alert tone="bad">{error}</Alert> : null}
    </section>
  );
}

// -- issue queue -----------------------------------------------------------------------------------

function FixResultNote({ result }: { result: WorkspaceFixResult }) {
  return (
    <div className="stack stack-sm" data-testid="workspace-fix-result">
      <Alert tone={result.applied.length > 0 ? "info" : "warn"}>
        {result.applied.length > 0
          ? `Fixed ${plural(result.applied.length, "issue")} automatically.`
          : "No automatic fix could be applied."}{" "}
        {result.skipped.length > 0 ? `${plural(result.skipped.length, "issue")} skipped.` : ""}
      </Alert>
      {result.skipped.length > 0 ? (
        <Disclosure summary="Why some issues were skipped">
          <ul className="stack stack-sm plain-list small">
            {result.skipped.map((item) => (
              <li key={item.finding_id}>
                {item.path ? <span className="mono">{item.path}</span> : null} {item.reason}
              </li>
            ))}
          </ul>
        </Disclosure>
      ) : null}
    </div>
  );
}

function IssueQueue({
  workspace,
  onChange,
  focus = [],
}: {
  workspace: Workspace;
  onChange: SetWorkspace;
  focus?: string[];
}) {
  const [query, setQuery] = useState<WorkspaceIssueQuery>(
    focus.length > 0 ? { issues: focus } : {},
  );
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // Pages loaded with "Show more" belong to one first page; a new first page drops them.
  const [more, setMore] = useState<{
    first: WorkspaceIssuePage | null;
    items: WorkspaceIssue[];
    cursor: string | null;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<WorkspaceFixResult | null>(null);
  const checkId = workspace.latest_check?.state ?? "";
  const page = useAsync(
    (signal) => listWorkspaceIssues(workspace.id, query, signal),
    [workspace.id, workspace.version, checkId, JSON.stringify(query)],
  );
  const loaded = more && more.first === page.data ? more : null;
  const items = useMemo(
    () => [...(page.data?.items ?? []), ...(loaded?.items ?? [])],
    [page.data, loaded],
  );
  const next = loaded ? loaded.cursor : (page.data?.next_cursor ?? null);
  const fixable = items.filter((item) => item.recipe_available);

  const fix = (selection: Parameters<typeof applyWorkspaceFixes>[1]) => {
    setBusy(true);
    setError(null);
    setResult(null);
    applyWorkspaceFixes(workspace, selection).then(
      (outcome) => {
        setBusy(false);
        setResult(outcome);
        setSelected(new Set());
        onChange(outcome.change_set);
      },
      (caught: unknown) => {
        setError(describeError(caught));
        setBusy(false);
      },
    );
  };

  if (!workspace.base_scan_id) {
    return (
      <section className="card">
        <Empty title="This upload has not been reviewed">
          <p className="small secondary">
            Review the upload to see its issues here. You can still edit files in the{" "}
            <a href={workspaceHref(workspace.id, "edit")}>Edit</a> tab.
          </p>
        </Empty>
      </section>
    );
  }
  return (
    <section className="card stack" aria-labelledby="ws-issues-title">
      <div className="card-head">
        <div>
          <h2 id="ws-issues-title" className="card-title">
            <Icon name="bug" size={16} /> Issues to fix
          </h2>
          <p className="card-sub">
            Select issues and fix them automatically where refactorX knows a safe fix, or open the
            file and fix it by hand.
          </p>
        </div>
      </div>
      {query.issues?.length ? (
        <div className="row" data-testid="workspace-focus">
          <span className="small">Showing the issues of one NFR checkpoint.</span>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => {
              setQuery({ ...query, issues: undefined });
            }}
          >
            Show all issues
          </button>
        </div>
      ) : null}
      <form
        className="row"
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery({ ...query, q: search.trim() || undefined });
        }}
      >
        <input
          className="search"
          type="search"
          placeholder="Search issues or files"
          aria-label="Search issues or files"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
          }}
        />
        <select
          aria-label="Severity"
          value={query.severity ?? ""}
          onChange={(event) => {
            setQuery({ ...query, severity: event.target.value || undefined });
          }}
        >
          <option value="">All severities</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
          <option value="info">Info</option>
        </select>
        <select
          aria-label="Result of the last check"
          value={query.outcome ?? ""}
          onChange={(event) => {
            const value = event.target.value as WorkspaceIssueQuery["outcome"] | "";
            setQuery({ ...query, outcome: value || undefined });
          }}
        >
          <option value="">Any result</option>
          <option value="still_present">Still present</option>
          <option value="fixed">Fixed</option>
          <option value="suppressed">Hidden, not fixed</option>
          <option value="unchecked">Not checked yet</option>
        </select>
        <label className="checkbox-row small">
          <input
            type="checkbox"
            checked={query.fixable === true}
            onChange={(event) => {
              setQuery({ ...query, fixable: event.target.checked ? true : undefined });
            }}
          />
          Automatic fix available
        </label>
      </form>
      {workspace.can_edit ? (
        <div className="row" data-testid="workspace-bulk">
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={busy || selected.size === 0}
            onClick={() => {
              fix({ finding_ids: [...selected] });
            }}
            data-testid="workspace-fix-selected"
          >
            <Icon name="wrench" size={14} />{" "}
            {busy ? "Fixing…" : `Fix ${String(selected.size)} selected automatically`}
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            disabled={fixable.length === 0}
            onClick={() => {
              setSelected(new Set(fixable.map((item) => item.finding_id)));
            }}
          >
            Select all with an automatic fix
          </button>
          {selected.size > 0 ? (
            <button
              type="button"
              className="link-button small"
              onClick={() => {
                setSelected(new Set());
              }}
            >
              Clear selection
            </button>
          ) : null}
        </div>
      ) : null}
      {result ? <FixResultNote result={result} /> : null}
      {error ? <Alert tone="bad">{error}</Alert> : null}
      {page.error ? <Alert tone="bad">{page.error}</Alert> : null}
      {!page.data ? (
        <Loading />
      ) : items.length === 0 ? (
        <Empty title="No issues match" />
      ) : (
        <div className="table-wrap">
          <table className="data-table" data-testid="workspace-issues">
            <caption className="visually-hidden">Issues of the upload</caption>
            <thead>
              <tr>
                {workspace.can_edit ? (
                  <th scope="col">
                    <span className="visually-hidden">Select</span>
                  </th>
                ) : null}
                <th scope="col">Issue</th>
                <th scope="col" className="only-wide">
                  Severity
                </th>
                <th scope="col" className="only-wide">
                  Last check
                </th>
                <th scope="col" className="only-wide">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.finding_id} data-finding={item.finding_id}>
                  {workspace.can_edit ? (
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Select ${item.title}`}
                        checked={selected.has(item.finding_id)}
                        onChange={(event) => {
                          const nextSet = new Set(selected);
                          if (event.target.checked) nextSet.add(item.finding_id);
                          else nextSet.delete(item.finding_id);
                          setSelected(nextSet);
                        }}
                      />
                    </td>
                  ) : null}
                  <th scope="row">
                    <div className="stack stack-xs">
                      <span>{item.title}</span>
                      <span className="cell-sub small">
                        <FileLocation path={item.path} line={item.line} />
                      </span>
                      {item.changed || item.recipe_available ? (
                        <span className="cell-sub small">
                          {[
                            item.changed ? "Changed in this workspace" : null,
                            item.recipe_available ? "Automatic fix available" : null,
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        </span>
                      ) : null}
                      {/* Narrow screens show the other columns here. */}
                      <span className="row only-narrow">
                        <SeverityChip severity={item.severity} />
                        <StatusBadge state={item.outcome ?? "unchecked"} />
                        <a
                          className="btn btn-ghost btn-sm"
                          href={workspaceHref(workspace.id, "edit", item.path, item.line)}
                        >
                          <Icon name="code" size={14} /> Edit
                        </a>
                      </span>
                    </div>
                  </th>
                  <td className="only-wide">
                    <SeverityChip severity={item.severity} />
                  </td>
                  <td className="only-wide">
                    <StatusBadge state={item.outcome ?? "unchecked"} />
                  </td>
                  <td className="cell-actions only-wide">
                    <div className="row row-nowrap">
                      <a
                        className="btn btn-ghost btn-sm"
                        href={workspaceHref(workspace.id, "edit", item.path, item.line)}
                      >
                        <Icon name="code" size={14} /> Edit
                      </a>
                      {item.recipe_available && workspace.can_edit ? (
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          disabled={busy}
                          title={`Fix every "${item.title}" issue (${checkName(item.engine)})`}
                          onClick={() => {
                            fix({ engine: item.engine, rule_id: item.rule_id });
                          }}
                        >
                          Fix all like this
                        </button>
                      ) : null}
                      {workspace.ai.available && workspace.can_edit && item.line ? (
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          disabled={busy}
                          onClick={() => {
                            setError(null);
                            requestAiFix(workspace.id, item.finding_id).then(
                              () => {
                                navigate(workspaceHref(workspace.id, "edit", item.path, item.line));
                              },
                              (caught: unknown) => {
                                setError(describeError(caught));
                              },
                            );
                          }}
                          data-testid="workspace-row-ask-ai"
                        >
                          <Icon name="sparkles" size={14} /> Ask AI
                        </button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {page.data ? (
        <div className="row">
          <span className="small muted">
            Showing {items.length} of {page.data.total}
          </span>
          {next ? (
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => {
                const first = page.data;
                listWorkspaceIssues(workspace.id, { ...query, cursor: next }).then(
                  (following) => {
                    setMore({
                      first,
                      items: [...(loaded?.items ?? []), ...following.items],
                      cursor: following.next_cursor ?? null,
                    });
                  },
                  (caught: unknown) => {
                    setError(describeError(caught));
                  },
                );
              }}
            >
              Show more
            </button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

// -- changes and comparison ------------------------------------------------------------------------

function useCompareLayout(): ["side" | "inline", (value: "side" | "inline") => void] {
  const [layout, setLayout] = useState<"side" | "inline">(() =>
    window.matchMedia("(min-width: 1100px)").matches ? "side" : "inline",
  );
  return [layout, setLayout];
}

function LayoutToggle({
  layout,
  onChange,
}: {
  layout: "side" | "inline";
  onChange: (value: "side" | "inline") => void;
}) {
  return (
    <div className="btn-group" role="group" aria-label="Comparison layout">
      <button
        type="button"
        className="btn btn-ghost btn-sm"
        aria-pressed={layout === "side"}
        onClick={() => {
          onChange("side");
        }}
      >
        Side by side
      </button>
      <button
        type="button"
        className="btn btn-ghost btn-sm"
        aria-pressed={layout === "inline"}
        onClick={() => {
          onChange("inline");
        }}
      >
        Inline
      </button>
    </div>
  );
}

function FileFlags({ flags }: { flags: string[] }) {
  if (flags.length === 0) return null;
  return (
    <Alert tone="warn">
      <ul className="stack stack-xs plain-list">
        {flags.map((flag) => (
          <li key={flag}>{editFlag(flag)}</li>
        ))}
      </ul>
    </Alert>
  );
}

function ChangeDetail({
  workspace,
  path,
  onChange,
}: {
  workspace: Workspace;
  path: string;
  onChange: SetWorkspace;
}) {
  const [layout, setLayout] = useCompareLayout();
  const [error, setError] = useState<string | null>(null);
  const file = useAsync(
    (signal) => fetchWorkspaceFile(workspace.id, path, signal),
    [workspace.id, path, workspace.version],
  );
  const row = workspace.files.find((item) => item.path === path);
  if (file.error) return <Alert tone="bad">{file.error}</Alert>;
  if (!file.data) return <Loading lines={6} />;
  return (
    <section className="card stack" aria-labelledby="ws-diff-title" data-testid="workspace-diff">
      <div className="card-head">
        <h2 id="ws-diff-title" className="card-title">
          <FileLocation path={path} />
        </h2>
        <LayoutToggle layout={layout} onChange={setLayout} />
      </div>
      <p className="small muted">
        {row ? (CHANGE_ACTIONS[row.action] ?? row.action) : "Unchanged"} ·{" "}
        {layout === "side"
          ? "Upload on the left, your version on the right."
          : "Upload above, your version below."}
      </p>
      {row ? <FileFlags flags={row.flags} /> : null}
      <CompareView
        path={path}
        before={file.data.base_content ?? ""}
        after={file.data.content ?? ""}
        layout={layout}
      />
      {workspace.can_edit && row ? (
        <div className="row">
          {row.action !== "delete" ? (
            <a className="btn btn-ghost btn-sm" href={workspaceHref(workspace.id, "edit", path)}>
              <Icon name="code" size={14} /> Edit
            </a>
          ) : null}
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => {
              setError(null);
              revertWorkspaceFile(workspace, path).then(
                (next) => {
                  onChange(next);
                  navigate(workspaceHref(workspace.id, "changes"));
                },
                (caught: unknown) => {
                  setError(describeError(caught));
                },
              );
            }}
            data-testid="workspace-revert"
          >
            <Icon name="arrowLeft" size={14} /> Undo changes to this file
          </button>
        </div>
      ) : null}
      {error ? <Alert tone="bad">{error}</Alert> : null}
    </section>
  );
}

function PullRequestCard({
  workspace,
  onChange,
}: {
  workspace: Workspace;
  onChange: SetWorkspace;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const state = workspace.pull_request;
  if (!state.applies) return null;
  const current = state.opened.find((pull) => pull.current);
  const earlier = state.opened.filter((pull) => !pull.current);
  return (
    <section
      className="card stack"
      aria-labelledby="ws-pr-title"
      data-testid="workspace-pull-request"
    >
      <h2 id="ws-pr-title" className="card-title">
        <Icon name="branch" size={16} /> Pull request on GitHub
      </h2>
      {current ? (
        <p className="row small">
          <a href={current.url} target="_blank" rel="noreferrer">
            Pull request #{current.number} on GitHub
          </a>
          <span className="muted">from {current.branch}</span>
        </p>
      ) : state.available ? (
        <div className="stack stack-xs">
          <div className="row">
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={busy || !workspace.can_edit}
              onClick={() => {
                setBusy(true);
                setError(null);
                openWorkspacePullRequest(workspace.id)
                  .then(() => fetchWorkspace(workspace.id))
                  .then(
                    (next) => {
                      setBusy(false);
                      onChange(next);
                    },
                    (caught: unknown) => {
                      setError(describeError(caught));
                      setBusy(false);
                    },
                  );
              }}
              data-testid="workspace-open-pull-request"
            >
              <Icon name="branch" size={14} /> {busy ? "Opening…" : "Open pull request"}
            </button>
          </div>
          <p className="small muted">
            One commit with all your changes on the reviewed branch, only if it has not moved since
            the review. Nothing is merged.
          </p>
        </div>
      ) : (
        <p className="small muted">{state.reason}</p>
      )}
      {earlier.length > 0 ? (
        <p className="small muted">
          Earlier:{" "}
          {earlier.map((pull, index) => (
            <span key={pull.number}>
              {index > 0 ? ", " : ""}
              <a href={pull.url} target="_blank" rel="noreferrer">
                #{pull.number}
              </a>
            </span>
          ))}{" "}
          (for an older version of your changes)
        </p>
      ) : null}
      {error ? <Alert tone="bad">{error}</Alert> : null}
    </section>
  );
}

function ChangesView({
  workspace,
  path,
  onChange,
}: {
  workspace: Workspace;
  path: string | null;
  onChange: SetWorkspace;
}) {
  if (workspace.files.length === 0) {
    return (
      <section className="card">
        <Empty title="No changes yet">
          <p className="small secondary">
            Fix issues automatically in the Issues tab, or edit a file in the Edit tab.
          </p>
        </Empty>
      </section>
    );
  }
  const current = path ?? workspace.files[0]?.path ?? null;
  return (
    <div className="stack">
      <PullRequestCard workspace={workspace} onChange={onChange} />
      <div className="split split-files">
        <section className="card stack" aria-labelledby="ws-files-title">
          <h2 id="ws-files-title" className="card-title">
            <Icon name="file" size={16} /> {plural(workspace.files.length, "changed file")}
          </h2>
          <ul className="stack stack-xs plain-list" data-testid="workspace-files">
            {workspace.files.map((file) => (
              <li key={file.path}>
                <a
                  className="result-item"
                  href={workspaceHref(workspace.id, "changes", file.path)}
                  aria-current={file.path === current ? "true" : undefined}
                  data-action={file.action}
                >
                  <span
                    className={`badge badge-${file.action === "delete" ? "bad" : file.action === "add" ? "ok" : "neutral"}`}
                  >
                    {CHANGE_ACTIONS[file.action] ?? file.action}
                  </span>
                  <span className="grow">
                    <FileLocation path={file.path} />
                  </span>
                  {file.flags.length > 0 ? (
                    <span className="status-icon status-warn" title="Needs attention">
                      <Icon name="alert" size={13} />
                      <span className="visually-hidden">Needs attention</span>
                    </span>
                  ) : null}
                </a>
              </li>
            ))}
          </ul>
          <Disclosure summary="History">
            <ul className="stack stack-sm plain-list small" data-testid="workspace-history">
              {workspace.events.map((event, index) => (
                <li key={index}>
                  <strong>{CHANGE_SOURCES[event.source] ?? event.source}</strong>{" "}
                  <span className="secondary">{event.summary}</span>
                  {event.path ? <span className="mono"> {event.path}</span> : null}{" "}
                  <span className="muted">{formatRelative(event.created_at)}</span>
                </li>
              ))}
            </ul>
          </Disclosure>
        </section>
        {current ? (
          <ChangeDetail key={current} workspace={workspace} path={current} onChange={onChange} />
        ) : null}
      </div>
    </div>
  );
}

// -- editor ----------------------------------------------------------------------------------------

function FilePicker({ workspace }: { workspace: Workspace }) {
  const [search, setSearch] = useState("");
  const [term, setTerm] = useState("");
  const [creating, setCreating] = useState("");
  const results = useAsync(
    (signal) =>
      term
        ? listFiles(workspace.base_snapshot_id, { q: term, disposition: "ANALYZABLE" }, signal)
        : Promise.resolve(null),
    [workspace.base_snapshot_id, term],
  );
  return (
    <section className="card stack" aria-labelledby="ws-pick-title" data-testid="workspace-picker">
      <h2 id="ws-pick-title" className="card-title">
        <Icon name="folder" size={16} /> Open a file
      </h2>
      <form
        className="row"
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          setTerm(search.trim());
        }}
      >
        <input
          className="grow"
          type="search"
          placeholder="Search files in the upload"
          aria-label="Search files in the upload"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
          }}
        />
        <button type="submit" className="btn btn-ghost btn-sm">
          Search
        </button>
      </form>
      {results.error ? <Alert tone="bad">{results.error}</Alert> : null}
      {results.data ? (
        results.data.items.length === 0 ? (
          <p className="small muted">No files match.</p>
        ) : (
          <ul className="result-list">
            {results.data.items.map((file) => (
              <li key={file.path}>
                <a className="result-item" href={workspaceHref(workspace.id, "edit", file.path)}>
                  <Icon name="file" size={14} />
                  <span className="mono">{file.path}</span>
                </a>
              </li>
            ))}
          </ul>
        )
      ) : null}
      {workspace.files.length > 0 ? (
        <div className="stack stack-xs">
          <span className="small muted">Changed in this workspace</span>
          <ul className="result-list">
            {workspace.files
              .filter((file) => file.action !== "delete")
              .map((file) => (
                <li key={file.path}>
                  <a className="result-item" href={workspaceHref(workspace.id, "edit", file.path)}>
                    <Icon name="code" size={14} />
                    <span className="mono">{file.path}</span>
                  </a>
                </li>
              ))}
          </ul>
        </div>
      ) : null}
      {workspace.can_edit ? (
        <Disclosure summary="Add a new file">
          <form
            className="row"
            onSubmit={(event) => {
              event.preventDefault();
              if (creating.trim()) navigate(workspaceHref(workspace.id, "edit", creating.trim()));
            }}
          >
            <input
              className="grow mono"
              placeholder="src/main/java/NewClass.java"
              aria-label="Path of the new file"
              value={creating}
              onChange={(event) => {
                setCreating(event.target.value);
              }}
            />
            <button type="submit" className="btn btn-ghost btn-sm">
              Create
            </button>
          </form>
        </Disclosure>
      ) : null}
    </section>
  );
}

function FileIssues({
  workspace,
  items,
  onLine,
  onAsk,
  asking,
}: {
  workspace: Workspace;
  items: WorkspaceIssue[];
  onLine: (line: number) => void;
  onAsk: (issue: WorkspaceIssue) => void;
  asking: string | null;
}) {
  if (items.length === 0) return null;
  const ai = workspace.ai.available && workspace.can_edit;
  return (
    <section
      className="card stack"
      aria-labelledby="ws-file-issues"
      data-testid="workspace-file-issues"
    >
      <h2 id="ws-file-issues" className="card-title">
        <Icon name="bug" size={16} /> Issues in this file
      </h2>
      <ul className="stack stack-sm plain-list">
        {items.map((item) => (
          <li key={item.finding_id} className="stack stack-xs">
            <button
              type="button"
              className="link-button small"
              onClick={() => {
                if (item.line) onLine(item.line);
              }}
            >
              {item.line ? `Line ${String(item.line)}: ` : ""}
              {item.title}
            </button>
            <span className="row">
              <SeverityChip severity={item.severity} />
              <StatusBadge state={item.outcome ?? "unchecked"} />
              {ai && item.line ? (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  disabled={asking !== null}
                  onClick={() => {
                    onAsk(item);
                  }}
                  data-testid="workspace-ask-ai"
                >
                  <Icon name="sparkles" size={14} />{" "}
                  {asking === item.finding_id ? "Asking…" : "Ask AI"}
                </button>
              ) : null}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

const CHECK_STEP: Record<string, { tone: string; icon: "check" | "x" | "info"; label: string }> = {
  passed: { tone: "ok", icon: "check", label: "Passed" },
  failed: { tone: "bad", icon: "x", label: "Failed" },
  not_run: { tone: "neutral", icon: "info", label: "Not run" },
};

const AI_ACTIVE = new Set(["QUEUED", "RUNNING"]);

function AiCandidateCard({
  workspace,
  run,
  candidate,
  dirty,
  onApplied,
}: {
  workspace: Workspace;
  run: AiRun;
  candidate: AiFixCandidate;
  dirty: boolean;
  onApplied: (next: Workspace) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const path = run.target_paths?.[0] ?? "";
  return (
    <li
      className="stack stack-sm ai-candidate"
      data-testid="ai-candidate"
      data-applicable={String(candidate.applicable)}
    >
      <div className="row">
        <span className="badge badge-live">
          <Icon name="sparkles" size={13} /> AI suggestion
        </span>
        <strong className="small">{candidate.title}</strong>
        <span className="small muted">{candidate.confidence} confidence</span>
      </div>
      <p className="small secondary">{candidate.explanation}</p>
      {candidate.behaviour_note ? (
        <p className="small">
          <strong>What to watch:</strong> {candidate.behaviour_note}
        </p>
      ) : null}
      {candidate.patch ? <DiffView patch={candidate.patch} path={path} /> : null}
      {candidate.steps.length > 0 ? (
        <ul className="stack stack-xs plain-list" aria-label="Checks of this suggestion">
          {candidate.steps.map((step) => {
            const known = CHECK_STEP[step.state] ?? CHECK_STEP.not_run;
            if (!known) return null;
            return (
              <li
                key={step.id}
                className="fix-step small"
                data-step={step.id}
                data-state={step.state}
              >
                <span className={`status-icon status-${known.tone}`}>
                  <Icon name={known.icon} size={13} />
                  <span className="visually-hidden">{known.label}</span>
                </span>
                <span>{step.label}</span>
              </li>
            );
          })}
        </ul>
      ) : null}
      {candidate.applied_at ? (
        <StatusBadge state="SUCCEEDED" label="Applied" />
      ) : candidate.applicable ? (
        workspace.can_edit ? (
          <div className="row">
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={busy || dirty}
              onClick={() => {
                setBusy(true);
                setError(null);
                applyAiFix(workspace, run.id, candidate.index).then(
                  (result) => {
                    onApplied(result.change_set);
                  },
                  (caught: unknown) => {
                    setError(describeError(caught));
                    setBusy(false);
                  },
                );
              }}
              data-testid="ai-candidate-apply"
            >
              {busy ? "Applying…" : "Apply to this file"}
            </button>
            {dirty ? <span className="small muted">Save or discard your edits first.</span> : null}
          </div>
        ) : null
      ) : (
        <Alert tone="warn">Not applied: {candidate.reason ?? candidate.summary}</Alert>
      )}
      <p className="hint">{candidate.label}</p>
      {error ? <Alert tone="bad">{error}</Alert> : null}
    </li>
  );
}

function AiSuggestions({
  workspace,
  path,
  issues,
  generation,
  dirty,
  onApplied,
}: {
  workspace: Workspace;
  path: string;
  issues: WorkspaceIssue[];
  generation: number;
  dirty: boolean;
  onApplied: (next: Workspace) => void;
}) {
  const [runs, setRuns] = useState<AiRun[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const active = (runs ?? []).some((run) => AI_ACTIVE.has(run.state));

  useEffect(() => {
    const controller = new AbortController();
    let timer = 0;
    const load = () => {
      listAiFixes(workspace.id, path, controller.signal).then(
        (next) => {
          setRuns(next);
          if (next.some((run) => AI_ACTIVE.has(run.state))) timer = window.setTimeout(load, 2000);
        },
        (caught: unknown) => {
          if (!controller.signal.aborted) setError(describeError(caught));
        },
      );
    };
    load();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [workspace.id, path, generation, active]);

  if (error) return <Alert tone="bad">{error}</Alert>;
  if (!runs || runs.length === 0) {
    return workspace.ai.available || issues.length === 0 ? null : (
      <p className="small muted" data-testid="workspace-ai-off">
        AI suggestions: {workspace.ai.reason}
      </p>
    );
  }
  const titles = new Map(issues.map((issue) => [issue.finding_id, issue.title]));
  return (
    <section className="card stack" aria-labelledby="ws-ai-title" data-testid="workspace-ai">
      <h2 id="ws-ai-title" className="card-title">
        <Icon name="sparkles" size={16} /> AI suggestions
      </h2>
      <p className="card-sub">
        Each suggestion was checked like an automatic fix: it must match this file, pass the change
        policy, still parse and make the check stop reporting the problem. Review it before
        applying.
      </p>
      {runs.slice(0, 5).map((run) => (
        <div
          key={run.id}
          className="stack stack-sm"
          data-testid="ai-fix-run"
          data-state={run.state}
        >
          <div className="row">
            <strong className="small">
              For: {(run.finding_id && titles.get(run.finding_id)) ?? "an issue in this file"}
            </strong>
            <StatusBadge
              state={run.state}
              {...(AI_ACTIVE.has(run.state) ? { label: "Preparing suggestions" } : {})}
            />
            {AI_ACTIVE.has(run.state) ? (
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => {
                  cancelAiRun(run.id).then(
                    () => listAiFixes(workspace.id, path).then(setRuns),
                    (caught: unknown) => {
                      setError(describeError(caught));
                    },
                  );
                }}
              >
                <Icon name="x" size={14} /> Stop
              </button>
            ) : null}
          </div>
          {run.error_message && !AI_ACTIVE.has(run.state) ? (
            <Alert tone={run.state === "CANCELED" ? "info" : "warn"}>{run.error_message}</Alert>
          ) : null}
          {run.state === "BUDGET_EXHAUSTED" && !run.fix ? (
            <Alert tone="warn">The AI reached its limit before suggesting a fix.</Alert>
          ) : null}
          {run.fix ? (
            run.fix.candidates.length === 0 ? (
              <p className="small muted">No suggestion: {run.fix.uncertainty || run.fix.text}</p>
            ) : (
              <ul className="stack plain-list">
                {run.fix.candidates.map((candidate) => (
                  <AiCandidateCard
                    key={candidate.index}
                    workspace={workspace}
                    run={run}
                    candidate={candidate}
                    dirty={dirty}
                    onApplied={onApplied}
                  />
                ))}
              </ul>
            )
          ) : null}
        </div>
      ))}
    </section>
  );
}

function conflictMessage(caught: unknown): string {
  if (caught instanceof ApiError && caught.code === "version_conflict") {
    return "This workspace changed in another tab or by a teammate. Copy your edit, reload and try again.";
  }
  return describeError(caught);
}

/** The editor works with LF line endings; the server keeps a CRLF file's endings on save. */
function lf(text: string): string {
  return text.replace(/\r\n?/g, "\n");
}

function FileEditor({
  workspace,
  file,
  line,
  onChange,
  onReload,
}: {
  workspace: Workspace;
  file: WorkspaceFileContent;
  line: number | null;
  onChange: SetWorkspace;
  onReload: () => void;
}) {
  // ``saved`` is the text last stored for this file; the editor stays mounted across saves.
  const [saved, setSaved] = useState(() => lf(file.content ?? ""));
  const [draft, setDraft] = useState(saved);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flags, setFlags] = useState<string[] | null>(null);
  const [jump, setJump] = useState<{ line: number; key: number } | null>(
    line ? { line, key: 0 } : null,
  );
  const [asking, setAsking] = useState<string | null>(null);
  const [aiGeneration, setAiGeneration] = useState(0);
  const fileIssues = useAsync(
    (signal) =>
      workspace.base_scan_id
        ? listWorkspaceIssues(workspace.id, { q: file.path }, signal)
        : Promise.resolve(null),
    [workspace.id, file.path, workspace.latest_check?.id],
  );
  const issues = (fileIssues.data?.items ?? []).filter((item) => item.path === file.path);
  const row = workspace.files.find((item) => item.path === file.path) ?? null;
  const dirty = draft !== saved;
  const isNew = row === null && file.base_content === null;

  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => {
      window.removeEventListener("beforeunload", warn);
    };
  }, [dirty]);

  const save = () => {
    if (!dirty || busy) return;
    const text = draft;
    setBusy(true);
    setError(null);
    saveWorkspaceFile(workspace, file.path, text).then(
      (result) => {
        setBusy(false);
        setSaved(text);
        setFlags(result.flags);
        onChange(result.change_set);
      },
      (caught: unknown) => {
        setError(conflictMessage(caught));
        setBusy(false);
      },
    );
  };

  return (
    <div className="split">
      <section
        className="card stack"
        aria-labelledby="ws-edit-title"
        data-testid="workspace-editor"
      >
        <div className="card-head">
          <h2 id="ws-edit-title" className="card-title">
            <FileLocation path={file.path} />
          </h2>
          <span className="small muted" data-testid="workspace-editor-state">
            {dirty
              ? "Unsaved changes"
              : row
                ? (CHANGE_ACTIONS[row.action] ?? row.action)
                : isNew
                  ? "New file"
                  : "As uploaded"}
          </span>
        </div>
        <CodeEditor
          key={`${file.path}:${String(jump?.key ?? 0)}`}
          path={file.path}
          value={draft}
          onChange={setDraft}
          onSave={save}
          readOnly={!workspace.can_edit}
          line={jump?.line ?? null}
        />
        {flags && flags.length > 0 && !dirty ? <FileFlags flags={flags} /> : null}
        {flags && flags.length === 0 && !dirty ? (
          <p className="small" role="status">
            <Icon name="check" size={14} /> Saved.
          </p>
        ) : null}
        {workspace.can_edit ? (
          <div className="row">
            <button
              type="button"
              className="btn btn-primary"
              disabled={!dirty || busy}
              onClick={save}
              data-testid="workspace-save"
            >
              {busy ? "Saving…" : "Save"}
            </button>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={!dirty || busy}
              onClick={() => {
                setDraft(saved);
                setJump({ line: jump?.line ?? 1, key: (jump?.key ?? 0) + 1 });
              }}
            >
              Discard edits
            </button>
            {row ? (
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                disabled={busy}
                onClick={() => {
                  revertWorkspaceFile(workspace, file.path).then(
                    (next) => {
                      onChange(next);
                      onReload();
                    },
                    (caught: unknown) => {
                      setError(conflictMessage(caught));
                    },
                  );
                }}
              >
                Undo all changes to this file
              </button>
            ) : null}
            {file.base_content !== null ? (
              <button
                type="button"
                className="btn btn-ghost btn-sm push-right"
                disabled={busy}
                onClick={() => {
                  deleteWorkspaceFile(workspace, file.path).then(
                    (next) => {
                      onChange(next);
                      navigate(workspaceHref(workspace.id, "changes", file.path));
                    },
                    (caught: unknown) => {
                      setError(conflictMessage(caught));
                    },
                  );
                }}
              >
                <Icon name="trash" size={14} /> Delete file
              </button>
            ) : null}
          </div>
        ) : null}
        <p className="hint">
          Press Ctrl+S or ⌘S to save. Markers that hide problems (for example eslint-disable or
          NOPMD) and skipped tests are flagged, and hidden problems never count as fixed.
        </p>
        {error ? (
          <Alert tone="bad">
            {error}{" "}
            {error.includes("reload") ? (
              <button type="button" className="link-button" onClick={onReload}>
                Reload
              </button>
            ) : null}
          </Alert>
        ) : null}
      </section>
      <div className="stack">
        <FileIssues
          workspace={workspace}
          items={issues}
          asking={asking}
          onLine={(target) => {
            setJump({ line: target, key: (jump?.key ?? 0) + 1 });
          }}
          onAsk={(issue) => {
            setAsking(issue.finding_id);
            setError(null);
            requestAiFix(workspace.id, issue.finding_id).then(
              () => {
                setAsking(null);
                setAiGeneration((value) => value + 1);
              },
              (caught: unknown) => {
                setAsking(null);
                setError(describeError(caught));
              },
            );
          }}
        />
        <AiSuggestions
          workspace={workspace}
          path={file.path}
          issues={issues}
          generation={aiGeneration}
          dirty={dirty}
          onApplied={(next) => {
            onChange(next);
            onReload();
          }}
        />
        <Disclosure testId="workspace-file-technical">
          <dl className="kv">
            <dt>Line endings</dt>
            <dd>{file.line_ending === "crlf" ? "Windows (CRLF), kept on save" : "Unix (LF)"}</dd>
            <dt>Uploaded content</dt>
            <dd className="hash">{shortHash(file.base_sha256 ?? "—", 24)}</dd>
          </dl>
        </Disclosure>
      </div>
    </div>
  );
}

const NEW_FILE = {
  action: null,
  editable: true,
  reason: null,
  language: null,
  base_sha256: null,
  base_content: null,
  sha256: null,
  content: null,
  line_ending: "lf" as const,
  flags: [],
};

/** Each fetched copy of a file gets its own number, so the editor remounts only on fresh data. */
let fileLoads = 0;

function EditView({
  workspace,
  path,
  line,
  onChange,
}: {
  workspace: Workspace;
  path: string | null;
  line: number | null;
  onChange: SetWorkspace;
}) {
  const [generation, setGeneration] = useState(0);
  const file = useAsync(
    (signal) =>
      path
        ? fetchWorkspaceFile(workspace.id, path, signal)
            .catch((caught: unknown): WorkspaceFileContent => {
              if (caught instanceof ApiError && caught.code === "file_not_found") {
                return { ...NEW_FILE, path };
              }
              throw caught;
            })
            .then((content) => ({ content, load: ++fileLoads }))
        : Promise.resolve(null),
    [workspace.id, path, generation],
  );
  const data = file.data?.content;
  return (
    <div className="stack">
      <FilePicker workspace={workspace} />
      {path ? (
        file.error ? (
          <Alert tone="bad">{file.error}</Alert>
        ) : !file.data || !data ? (
          <Loading lines={8} />
        ) : !data.editable ? (
          <Alert tone="info">{data.reason ?? "This file cannot be edited here."}</Alert>
        ) : data.action === "delete" ? (
          <Alert tone="info">
            This file is deleted in this workspace. Undo it in the{" "}
            <a href={workspaceHref(workspace.id, "changes", path)}>Changes</a> tab.
          </Alert>
        ) : (
          <FileEditor
            key={String(file.data.load)}
            workspace={workspace}
            file={data}
            line={line}
            onChange={onChange}
            onReload={() => {
              setGeneration((value) => value + 1);
              void fetchWorkspace(workspace.id).then(onChange, () => undefined);
            }}
          />
        )
      ) : null}
    </div>
  );
}

// -- page ------------------------------------------------------------------------------------------

export function WorkspacePage({
  workspaceId,
  tab,
  path,
  line,
  focus = [],
}: {
  workspaceId: string;
  tab: string;
  path: string | null;
  line: number | null;
  focus?: string[];
}) {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const active = checkActive(workspace?.latest_check);

  useEffect(() => {
    const controller = new AbortController();
    let timer = 0;
    const load = () => {
      fetchWorkspace(workspaceId, controller.signal).then(
        (next) => {
          setWorkspace(next);
          if (checkActive(next.latest_check)) timer = window.setTimeout(load, 2000);
        },
        (caught: unknown) => {
          if (!controller.signal.aborted) setError(describeError(caught));
        },
      );
    };
    load();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [workspaceId, active]);

  if (error) return <Alert tone="bad">{error}</Alert>;
  if (!workspace) return <Loading lines={6} />;
  const base = `#/workspaces/${workspace.id}`;
  return (
    <>
      <PageHeader
        eyebrow={
          <a href={`#/projects/${workspace.project_id}?tab=workspaces`}>
            <Icon name="arrowLeft" size={14} /> Back to the project
          </a>
        }
        title={workspace.title}
        sub={`Your fixes on top of ${workspace.base_name}. The upload itself is never changed.`}
        actions={
          <>
            <StatusBadge
              state={workspace.state}
              {...(workspace.state === "ready" ? { label: "Checked" } : {})}
            />
            <ExportMenu workspace={workspace} />
          </>
        }
      />
      {!workspace.can_edit ? (
        <Alert tone="info">You can view this workspace; editing needs a member role.</Alert>
      ) : null}
      <Tabs
        current={tab}
        items={[
          { id: "issues", label: "Issues", href: base },
          {
            id: "changes",
            label: `Changes (${String(workspace.files.length)})`,
            href: `${base}?tab=changes`,
          },
          { id: "edit", label: "Edit", href: `${base}?tab=edit` },
        ]}
      />
      {tab === "issues" ? (
        <div className="stack">
          <CheckCard workspace={workspace} onChange={setWorkspace} />
          <IssueQueue workspace={workspace} onChange={setWorkspace} focus={focus} />
        </div>
      ) : null}
      {tab === "changes" ? (
        <ChangesView workspace={workspace} path={path} onChange={setWorkspace} />
      ) : null}
      {tab === "edit" ? (
        <EditView workspace={workspace} path={path} line={line} onChange={setWorkspace} />
      ) : null}
      <Disclosure testId="workspace-technical">
        <dl className="kv">
          <dt>Upload</dt>
          <dd className="hash">{shortHash(workspace.base_snapshot_id, 13)}</dd>
          {workspace.base_git_commit ? (
            <>
              <dt>Commit</dt>
              <dd className="hash">{shortHash(workspace.base_git_commit, 12)}</dd>
            </>
          ) : null}
          <dt>Content of your changes</dt>
          <dd className="hash">{shortHash(workspace.content_sha256, 24)}</dd>
          <dt>Created</dt>
          <dd>{formatDate(workspace.created_at)}</dd>
          <dt>Updated</dt>
          <dd>{formatDate(workspace.updated_at)}</dd>
        </dl>
        {workspace.files.length > 0 ? (
          <p className="small">
            <a
              href={workspaceExportUrl(workspace.id, "mbox")}
              download
              data-testid="workspace-export-mbox"
            >
              Download as one commit
            </a>{" "}
            for <code>git am</code> (author refactorX; amend it to take ownership).
          </p>
        ) : null}
        <p className="hint">
          Downloads name the exact upload they apply to. Apply the patch with{" "}
          <code>git apply -p1</code> in a copy of exactly this upload; it does not apply to code
          that changed since.
        </p>
      </Disclosure>
    </>
  );
}
