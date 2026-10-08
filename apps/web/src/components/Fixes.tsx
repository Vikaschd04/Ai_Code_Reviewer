import { useState } from "react";

import { describeError, type FixProposal } from "../api/client";
import {
  createFix,
  createWorkspace,
  fetchFixOptions,
  listFixes,
  listWorkspaces,
} from "../api/endpoints";
import { formatRelative } from "../lib/format";
import { navigate, workspaceHref } from "../lib/router";
import { useAsync } from "../lib/useAsync";
import { Alert } from "./Common";
import { Icon } from "./Icon";
import { StatusBadge } from "./Status";

interface DiffLine {
  kind: "context" | "add" | "del" | "hunk" | "note";
  text: string;
  oldLine: number | null;
  newLine: number | null;
}

const HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/;

/** Parse a unified diff for display (file headers are dropped; text is shown as inert text). */
export function parseDiff(patch: string): DiffLine[] {
  const lines: DiffLine[] = [];
  let oldLine = 0;
  let newLine = 0;
  for (const raw of patch.split("\n")) {
    if (raw.startsWith("diff --git") || raw.startsWith("--- ") || raw.startsWith("+++ ")) continue;
    const hunk = HUNK.exec(raw);
    if (hunk) {
      oldLine = Number(hunk[1]);
      newLine = Number(hunk[2]);
      lines.push({ kind: "hunk", text: raw, oldLine: null, newLine: null });
    } else if (raw.startsWith("+")) {
      lines.push({ kind: "add", text: raw.slice(1), oldLine: null, newLine: newLine++ });
    } else if (raw.startsWith("-")) {
      lines.push({ kind: "del", text: raw.slice(1), oldLine: oldLine++, newLine: null });
    } else if (raw.startsWith("\\")) {
      lines.push({ kind: "note", text: raw.slice(2), oldLine: null, newLine: null });
    } else if (raw.startsWith(" ")) {
      lines.push({ kind: "context", text: raw.slice(1), oldLine: oldLine++, newLine: newLine++ });
    }
  }
  return lines;
}

const SIGN: Record<DiffLine["kind"], string> = {
  context: " ",
  add: "+",
  del: "−",
  hunk: "",
  note: "",
};

export function DiffView({ patch, path }: { patch: string; path: string }) {
  const lines = parseDiff(patch);
  return (
    <div className="diff" role="table" aria-label={`Changes to ${path}`} data-testid="diff">
      {lines.map((line, index) => (
        <div key={index} role="row" className={`diff-line diff-${line.kind}`} data-kind={line.kind}>
          <span role="cell" className="diff-no">
            {line.oldLine ?? ""}
          </span>
          <span role="cell" className="diff-no">
            {line.newLine ?? ""}
          </span>
          <span role="cell" className="diff-sign" aria-hidden="true">
            {SIGN[line.kind]}
          </span>
          <span role="cell" className="diff-text">
            {line.kind === "add" ? <span className="visually-hidden">Added: </span> : null}
            {line.kind === "del" ? <span className="visually-hidden">Removed: </span> : null}
            {line.text}
          </span>
        </div>
      ))}
    </div>
  );
}

/** A fix proposal's state (VALIDATING means "checking the fix", not an upload check). */
export function FixStatus({ state }: { state: string }) {
  return state === "VALIDATING" ? (
    <StatusBadge state={state} label="Checking" />
  ) : (
    <StatusBadge state={state} />
  );
}

/** Finding page: prepare a fix from a deterministic recipe and see earlier fixes. */
export function FixCard({ projectId, findingId }: { projectId: string; findingId: string }) {
  const options = useAsync((signal) => fetchFixOptions(findingId, signal), [findingId]);
  const fixes = useAsync(
    (signal) => listFixes(projectId, signal, findingId),
    [projectId, findingId],
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const choices = options.data?.options ?? [];
  const earlier: FixProposal[] = fixes.data ?? [];
  if (choices.length === 0 && earlier.length === 0) return null;
  return (
    <section className="card stack" aria-labelledby="fix-title" data-testid="fix-card">
      <div className="stack stack-xs">
        <h2 id="fix-title" className="card-title">
          <Icon name="wrench" size={16} /> Fix
        </h2>
        <p className="card-sub">
          An automatic fix changes a copy of your code; your upload stays as it is. You can review
          and edit it, run checks and download it as a patch.
        </p>
      </div>
      {choices.map((option) =>
        option.available ? (
          <div className="row" key={option.recipe_id}>
            <button
              type="button"
              className="btn btn-ghost"
              disabled={busy !== null}
              onClick={() => {
                setBusy(option.recipe_id);
                setError(null);
                createFix(findingId, option.recipe_id).then(
                  (fix) => {
                    navigate(`#/fixes/${fix.id}`);
                  },
                  (caught: unknown) => {
                    setError(describeError(caught));
                    setBusy(null);
                  },
                );
              }}
            >
              <Icon name="wrench" size={15} />{" "}
              {busy === option.recipe_id ? "Preparing…" : `Prepare fix: ${option.title}`}
            </button>
          </div>
        ) : (
          <p key={option.recipe_id} className="small muted">
            No automatic fix for this occurrence: {option.reason}
          </p>
        ),
      )}
      {earlier.length > 0 ? (
        <ul className="stack stack-sm plain-list" data-testid="fix-history">
          {earlier.map((fix) => (
            <li key={fix.id} className="row row-between">
              <a href={`#/fixes/${fix.id}`} className="small">
                {fix.title}
              </a>
              <span className="row">
                <FixStatus state={fix.state} />
                <span className="small muted">{formatRelative(fix.created_at)}</span>
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {error ? <Alert tone="bad">{error}</Alert> : null}
    </section>
  );
}

/** Open the finding's file in a fix workspace on the same upload (reusing the newest one). */
export function WorkspaceCard({
  projectId,
  snapshotId,
  path,
  line,
}: {
  projectId: string;
  snapshotId: string;
  path: string;
  line: number | null;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <section className="card stack" aria-labelledby="ws-card-title" data-testid="workspace-card">
      <div className="stack stack-xs">
        <h2 id="ws-card-title" className="card-title">
          <Icon name="code" size={16} /> Fix in a workspace
        </h2>
        <p className="card-sub">
          Edit this file together with other fixes, check them all at once and download one patch or
          only the changed files.
        </p>
      </div>
      <div className="row">
        <button
          type="button"
          className="btn btn-ghost"
          disabled={busy}
          data-testid="workspace-open"
          onClick={() => {
            setBusy(true);
            setError(null);
            listWorkspaces(projectId)
              .then(async (items): Promise<string> => {
                const existing = items.find((item) => item.base_snapshot_id === snapshotId);
                if (existing) return existing.id;
                return (await createWorkspace(projectId, { snapshot_id: snapshotId })).id;
              })
              .then(
                (workspaceId) => {
                  navigate(workspaceHref(workspaceId, "edit", path, line));
                },
                (caught: unknown) => {
                  setError(describeError(caught));
                  setBusy(false);
                },
              );
          }}
        >
          <Icon name="code" size={15} /> {busy ? "Opening…" : "Open in workspace"}
        </button>
      </div>
      {error ? <Alert tone="bad">{error}</Alert> : null}
    </section>
  );
}
