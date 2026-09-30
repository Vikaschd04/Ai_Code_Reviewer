import { useEffect, useState } from "react";

import { describeError, type FixProposal } from "../api/client";
import {
  cancelFixValidation,
  editFix,
  fetchFix,
  fixDownloadUrl,
  rejectFix,
  validateFix,
} from "../api/endpoints";
import { Alert, Disclosure, Loading, PageHeader } from "../components/Common";
import { FileLocation } from "../components/FileLocation";
import { DiffView, FixStatus } from "../components/Fixes";
import { Icon, type IconName } from "../components/Icon";
import { SeverityChip } from "../components/Severity";
import { StatusBadge } from "../components/Status";
import { formatDate, shortHash } from "../lib/format";

const STEP: Record<string, { icon: IconName; tone: string; label: string }> = {
  passed: { icon: "check", tone: "ok", label: "Passed" },
  failed: { icon: "x", tone: "bad", label: "Failed" },
  not_run: { icon: "info", tone: "neutral", label: "Not run" },
};

function running(fix: FixProposal): boolean {
  const state = fix.latest_validation?.state;
  return state === "QUEUED" || state === "RUNNING";
}

function Checks({ fix, onChange }: { fix: FixProposal; onChange: (next: FixProposal) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const validation = fix.latest_validation;
  const left = fix.max_validations - fix.validations_used;
  const active = running(fix);
  const current = validation?.current === true;
  return (
    <section className="card stack" aria-labelledby="checks-title" data-testid="fix-checks">
      <div className="card-head">
        <div>
          <h2 id="checks-title" className="card-title">
            <Icon name="shield" size={16} /> Checks
          </h2>
          <p className="card-sub">
            refactorX applies the fix to a copy, checks it parses and re-runs the checks that found
            the problem. It does not run your tests or build.
          </p>
        </div>
        {validation ? <StatusBadge state={active ? "RUNNING" : validation.state} /> : null}
      </div>
      {validation && !current && !active ? (
        <Alert tone="info">These results are for an earlier version of this fix.</Alert>
      ) : null}
      {validation ? (
        <ol className="stack stack-sm plain-list" aria-label="Validation steps">
          {validation.steps.map((step) => {
            const known = STEP[step.state] ?? STEP.not_run;
            if (!known) return null;
            return (
              <li key={step.id} className="fix-step" data-step={step.id} data-state={step.state}>
                <span className={`status-icon status-${known.tone}`}>
                  <Icon name={known.icon} size={13} />
                  <span className="visually-hidden">{known.label}</span>
                </span>
                <span className="stack stack-xs">
                  <strong className="small">{step.label}</strong>
                  <span className="small secondary">{step.detail}</span>
                </span>
              </li>
            );
          })}
        </ol>
      ) : (
        <p className="small muted">Not checked yet.</p>
      )}
      {validation?.summary && !active ? (
        <p className="small" data-testid="fix-summary">
          {validation.summary}
        </p>
      ) : null}
      {active ? (
        <div className="row">
          <span className="pulse-dot" />
          <span className="small">Checking the fix…</span>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => {
              if (!validation) return;
              cancelFixValidation(validation.id).then(
                () => fetchFix(fix.id).then(onChange),
                (caught: unknown) => {
                  setError(describeError(caught));
                },
              );
            }}
          >
            <Icon name="x" size={14} /> Stop
          </button>
        </div>
      ) : fix.state !== "REJECTED" ? (
        <div className="row">
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy || left <= 0}
            onClick={() => {
              setBusy(true);
              setError(null);
              validateFix(fix.id)
                .then(() => fetchFix(fix.id))
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
          >
            <Icon name="play" size={15} /> {current ? "Check again" : "Run checks"}
          </button>
          <span className="small muted">
            {left > 0
              ? `${String(left)} of ${String(fix.max_validations)} checks left`
              : "No checks left"}
          </span>
        </div>
      ) : null}
      {error ? <Alert tone="bad">{error}</Alert> : null}
    </section>
  );
}

function Editor({ fix, onChange }: { fix: FixProposal; onChange: (next: FixProposal) => void }) {
  const [drafts, setDrafts] = useState(() => fix.edits.map((edit) => edit.replacement.join("\n")));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <Disclosure summary="Edit the fix" testId="fix-editor">
      <form
        className="form-grid"
        onSubmit={(event) => {
          event.preventDefault();
          setBusy(true);
          setError(null);
          editFix(
            fix,
            fix.edits.map((edit, index) => ({
              start_line: edit.start_line,
              replacement: (drafts[index] ?? "").split("\n"),
            })),
          ).then(
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
      >
        {fix.edits.map((edit, index) => (
          <label key={edit.start_line}>
            New code for line {edit.start_line}
            {edit.end_line > edit.start_line ? `–${String(edit.end_line)}` : ""}
            <textarea
              className="mono"
              rows={Math.min(12, Math.max(2, (drafts[index] ?? "").split("\n").length + 1))}
              value={drafts[index] ?? ""}
              onChange={(event) => {
                const next = [...drafts];
                next[index] = event.target.value;
                setDrafts(next);
              }}
            />
          </label>
        ))}
        <p className="hint">
          Changes that silence a check (for example NOPMD or eslint-disable) or weaken tests are
          refused. Saving resets the checks.
        </p>
        <div className="row">
          <button type="submit" className="btn btn-ghost" disabled={busy}>
            {busy ? "Saving…" : "Save changes"}
          </button>
        </div>
        {error ? <Alert tone="bad">{error}</Alert> : null}
      </form>
    </Disclosure>
  );
}

export function FixPage({ fixId }: { fixId: string }) {
  const [fix, setFix] = useState<FixProposal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const active = fix ? running(fix) : false;

  useEffect(() => {
    const controller = new AbortController();
    let timer = 0;
    const load = () => {
      fetchFix(fixId, controller.signal).then(
        (next) => {
          setFix(next);
          if (running(next)) timer = window.setTimeout(load, 1500);
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
  }, [fixId, active]);

  if (error) return <Alert tone="bad">{error}</Alert>;
  if (!fix) return <Loading lines={6} />;
  const finding = fix.finding;
  return (
    <>
      <PageHeader
        eyebrow={
          <a href={`#/findings/${fix.finding_id}`}>
            <Icon name="arrowLeft" size={14} /> Back to the finding
          </a>
        }
        title={fix.title}
        sub={finding ? `Fix for: ${finding.title}` : undefined}
        actions={
          <>
            {finding ? <SeverityChip severity={finding.severity} /> : null}
            <FixStatus state={fix.state} />
            <div className="btn-group" role="group" aria-label="Download">
              <a
                className="btn btn-ghost btn-sm"
                href={fixDownloadUrl(fix.id, "patch")}
                download
                data-testid="fix-download-patch"
              >
                <Icon name="download" size={14} /> Patch
              </a>
              <a
                className="btn btn-ghost btn-sm"
                href={fixDownloadUrl(fix.id, "summary")}
                download
                data-testid="fix-download-summary"
              >
                Summary
              </a>
            </div>
          </>
        }
      />
      <div className="split">
        <div className="stack">
          <section className="card stack" aria-labelledby="change-title">
            <div className="card-head">
              <h2 id="change-title" className="card-title">
                <FileLocation path={fix.path} />
              </h2>
              <span className="small muted">
                {fix.changed_lines} line{fix.changed_lines === 1 ? "" : "s"} changed
                {fix.edited ? " · edited" : ""}
              </span>
            </div>
            <p className="secondary">{fix.explanation}</p>
            <DiffView patch={fix.patch} path={fix.path} />
            {fix.behaviour_note ? (
              <Alert tone="warn">
                <strong>What to watch:</strong> {fix.behaviour_note}
              </Alert>
            ) : null}
            {fix.state !== "REJECTED" ? (
              <Editor key={fix.version} fix={fix} onChange={setFix} />
            ) : null}
          </section>
          <Checks fix={fix} onChange={setFix} />
        </div>
        <div className="stack">
          <section className="card stack" aria-labelledby="fix-status-title">
            <h2 id="fix-status-title" className="card-title">
              <Icon name="info" size={16} /> What this fix is
            </h2>
            <ul className="stack stack-sm plain-list small secondary" data-testid="fix-labels">
              {fix.labels.map((label) => (
                <li key={label}>{label}</li>
              ))}
            </ul>
            <p className="hint">
              Apply the downloaded patch with <code>git apply -p1</code> in a copy of exactly this
              upload, then build and test it in your own environment.
            </p>
            {fix.state === "REJECTED" ? (
              <Alert tone="info">Rejected: {fix.rejected_reason}</Alert>
            ) : rejecting ? (
              <form
                className="form-grid"
                onSubmit={(event) => {
                  event.preventDefault();
                  rejectFix(fix.id, reason.trim() || "Not wanted").then(
                    setFix,
                    (caught: unknown) => {
                      setError(describeError(caught));
                    },
                  );
                }}
              >
                <label>
                  Why reject it?
                  <input
                    value={reason}
                    maxLength={500}
                    onChange={(event) => {
                      setReason(event.target.value);
                    }}
                  />
                </label>
                <div className="row">
                  <button type="submit" className="btn btn-danger btn-sm">
                    Reject fix
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() => {
                      setRejecting(false);
                    }}
                  >
                    Cancel
                  </button>
                </div>
              </form>
            ) : (
              <div className="row">
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => {
                    setRejecting(true);
                  }}
                >
                  Reject
                </button>
              </div>
            )}
          </section>
          <Disclosure testId="fix-technical">
            <dl className="kv">
              <dt>Fix type</dt>
              <dd className="mono">{fix.recipe_id}</dd>
              <dt>Upload</dt>
              <dd className="hash">{shortHash(fix.snapshot_id, 13)}</dd>
              <dt>File before</dt>
              <dd className="hash">{shortHash(fix.base_sha256, 24)}</dd>
              <dt>File after</dt>
              <dd className="hash">{shortHash(fix.result_sha256, 24)}</dd>
              <dt>Patch</dt>
              <dd className="hash">{shortHash(fix.patch_sha256, 24)}</dd>
              <dt>Rule</dt>
              <dd className="mono">{finding ? `${finding.engine} ${finding.rule_id}` : "—"}</dd>
              <dt>Prepared</dt>
              <dd>{formatDate(fix.created_at)}</dd>
            </dl>
          </Disclosure>
        </div>
      </div>
    </>
  );
}
