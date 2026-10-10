/** Insights (ADR 0024): the project's NFR checkpoints by area — what the uploaded code and
 * configuration show for each non-functional requirement, and how to resolve what needs work.
 * The tools decide every status; the optional AI plan orders the work, with every step checked
 * against the same evidence. Architecture is a second view of the same tab. */
import { useEffect, useState } from "react";

import {
  describeError,
  type AiRun,
  type Checkpoint,
  type EvidenceItem,
  type Insights,
} from "../api/client";
import {
  clearHandled,
  createWorkspace,
  fetchAiRun,
  fetchInsights,
  listWorkspaces,
  markHandled,
  requestPlan,
} from "../api/endpoints";
import { isTerminalRun } from "../components/Ai";
import { ArchitectureRules } from "../components/ArchitectureRules";
import { Alert, Disclosure, Empty, Loading } from "../components/Common";
import { Icon, type IconName } from "../components/Icon";
import { SeverityChip } from "../components/Severity";
import { formatRelative } from "../lib/format";
import { plural } from "../lib/labels";
import { navigate, workspaceHref } from "../lib/router";
import { useAsync } from "../lib/useAsync";
import { AiQuestions } from "./AiView";
import { ArchitectureView } from "./ArchitectureView";

const UNKNOWN_STATE = { label: "Not enough evidence", tone: "neutral", icon: "info" as IconName };
const AREA_STATE: Record<string, { label: string; tone: string; icon: IconName }> = {
  attention: { label: "Needs attention", tone: "warn", icon: "alert" },
  improve: { label: "Could be better", tone: "live", icon: "info" },
  no_problems: { label: "No problems found", tone: "ok", icon: "check" },
  unknown: UNKNOWN_STATE,
};
const NOT_CHECKED = { label: "Not checked", tone: "neutral", icon: "info" as IconName };
const STATUS: Record<string, { label: string; tone: string; icon: IconName }> = {
  attention: { label: "Needs attention", tone: "warn", icon: "alert" },
  missing: { label: "Not found", tone: "live", icon: "info" },
  in_place: { label: "In place", tone: "ok", icon: "check" },
  handled: { label: "Handled elsewhere", tone: "ok", icon: "check" },
  no_issues: { label: "No issues found", tone: "ok", icon: "check" },
  not_checked: NOT_CHECKED,
  not_applicable: { label: "Not applicable", tone: "neutral", icon: "info" },
};
const LOW_PRIORITY = { label: "Low priority", tone: "neutral" };
const PRIORITY: Record<string, { label: string; tone: string }> = {
  high: { label: "High priority", tone: "warn" },
  medium: { label: "Medium priority", tone: "live" },
  low: LOW_PRIORITY,
};
const EFFORT: Record<string, string> = { small: "Small", medium: "Medium", large: "Large" };
const FAILING = new Set(["attention", "missing"]);

function Chip({ tone, icon, children }: { tone: string; icon?: IconName; children: string }) {
  return (
    <span className={`badge badge-${tone}`}>
      {icon ? <Icon name={icon} size={12} /> : null} {children}
    </span>
  );
}

function StatusChip({ status }: { status: string }) {
  const look = STATUS[status] ?? NOT_CHECKED;
  return (
    <Chip tone={look.tone} icon={look.icon}>
      {look.label}
    </Chip>
  );
}

function areaName(data: Insights, id: string): string {
  return data.areas.find((a) => a.id === id)?.name ?? id;
}

function needsWork(counts: Record<string, number>): number {
  return (counts.attention ?? 0) + (counts.missing ?? 0);
}

function passed(counts: Record<string, number>): number {
  return (counts.in_place ?? 0) + (counts.handled ?? 0) + (counts.no_issues ?? 0);
}

function AreaList({ data }: { data: Insights }) {
  return (
    <ul className="stack stack-xs plain-list" aria-label="Health by area">
      {data.areas.map((area) => {
        const look = AREA_STATE[area.state] ?? UNKNOWN_STATE;
        return (
          <li key={area.id} className="row" data-area={area.id} data-state={area.state}>
            <span className="small">{area.name}</span>
            <Chip tone={look.tone} icon={look.icon}>
              {look.label}
            </Chip>
          </li>
        );
      })}
    </ul>
  );
}

function AreaTiles({ data }: { data: Insights }) {
  return (
    <div className="tiles tiles-3" role="group" aria-label="Health by area">
      {data.areas.map((area) => {
        const look = AREA_STATE[area.state] ?? UNKNOWN_STATE;
        const open = needsWork(area.counts);
        const done = passed(area.counts);
        return (
          <button
            key={area.id}
            type="button"
            className="tile"
            data-area={area.id}
            data-state={area.state}
            onClick={() => {
              document.getElementById(`area-${area.id}`)?.scrollIntoView({ behavior: "smooth" });
            }}
          >
            <span className="small">
              <strong>{area.name}</strong>
            </span>
            <span className="tile-label">
              <Chip tone={look.tone} icon={look.icon}>
                {look.label}
              </Chip>
            </span>
            <span className="small muted">
              {open > 0 ? `${String(open)} to resolve` : "Nothing to resolve"}
              {done > 0 ? ` · ${String(done)} passed` : ""}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function EvidenceList({ items, testId }: { items: EvidenceItem[]; testId: string }) {
  return (
    <ul className="stack stack-xs plain-list" data-testid={testId}>
      {items.map((item) => (
        <li key={item.signal} className="stack stack-xs">
          <span className="small">
            <strong>{item.label}</strong>
            {item.count > item.locations.length ? ` · ${plural(item.count, "place")}` : ""}
          </span>
          {item.locations.map((where) => (
            <span
              key={`${where.path}:${String(where.line)}:${where.detail ?? ""}`}
              className="mono small secondary wrap-anywhere"
            >
              {where.line ? `${where.path}:${String(where.line)}` : where.path}
              {where.detail ? ` — ${where.detail}` : ""}
            </span>
          ))}
        </li>
      ))}
    </ul>
  );
}

function StartFixing({
  projectId,
  data,
  insight,
}: {
  projectId: string;
  data: Insights;
  insight: Checkpoint;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const snapshotId = data.basis?.snapshot_id;
  if (!snapshotId || insight.issue_ids.length === 0) return null;
  return (
    <>
      <button
        type="button"
        className={insight.priority === "high" ? "btn btn-primary" : "btn btn-ghost"}
        disabled={busy}
        data-testid="checkpoint-start-fixing"
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
              (id) => {
                navigate(workspaceHref(id, "issues", null, null, insight.issue_ids));
              },
              (caught: unknown) => {
                setError(describeError(caught));
                setBusy(false);
              },
            );
        }}
      >
        <Icon name="wrench" size={15} /> {busy ? "Opening…" : "Start fixing"}
      </button>
      {error ? <Alert tone="bad">{error}</Alert> : null}
    </>
  );
}

function PlanView({ run, data }: { run: AiRun; data: Insights }) {
  const plan = run.plan;
  if (!plan) return null;
  const titles = new Map(data.checkpoints.map((c) => [c.id, c.title]));
  return (
    <div className="stack" data-testid="advisor-plan">
      <p className="small">{plan.summary}</p>
      {plan.steps.length > 0 ? (
        <ol className="stack stack-sm" data-testid="advisor-steps">
          {plan.steps.map((step) => (
            <li key={step.title} className="stack stack-xs">
              <span className="row">
                <strong className="small">{step.title}</strong>
                <Chip tone="neutral">{areaName(data, step.area)}</Chip>
                <span className="small muted">Effort: {EFFORT[step.effort] ?? step.effort}</span>
              </span>
              <span className="small secondary">{step.rationale}</span>
              <span className="small muted">
                Based on:{" "}
                {[
                  ...step.insight_ids.map((id) => titles.get(id) ?? id),
                  ...step.anchors.map(
                    (a) =>
                      `${a.path}:${String(a.start_line)}${a.end_line > a.start_line ? `-${String(a.end_line)}` : ""}`,
                  ),
                ].join("; ") || `${plural(step.fact_ids.length, "fact")} from the tools`}
              </span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="small muted">
          The advisor did not propose steps{plan.uncertainty ? `: ${plan.uncertainty}` : "."}
        </p>
      )}
      {plan.rejected.length > 0 ? (
        <Disclosure
          summary={`${plural(plan.rejected.length, "suggestion")} removed for lack of evidence`}
          testId="advisor-rejected"
        >
          <ul className="stack stack-xs plain-list">
            {plan.rejected.map((item) => (
              <li key={`${item.title}:${item.reason}`} className="small">
                <strong>{item.title}</strong> — {item.reason}
              </li>
            ))}
          </ul>
        </Disclosure>
      ) : null}
      <p className="hint">
        Written by AI ({run.model}) {formatRelative(run.finished_at ?? run.created_at)}. Every step
        was checked against the checkpoints, the tools&apos; facts and the cited code; priorities
        and counts come from the tools, never from AI.
      </p>
    </div>
  );
}

function AdvisorCard({
  data,
  projectId,
  onDone,
}: {
  data: Insights;
  projectId: string;
  onDone: () => void;
}) {
  const [running, setRunning] = useState<AiRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const latest = data.advisor.latest;
  const active = running ?? (latest && !isTerminalRun(latest.state) ? latest : null);
  useEffect(() => {
    if (!active) return undefined;
    const timer = window.setTimeout(() => {
      fetchAiRun(active.id).then(
        (next) => {
          if (isTerminalRun(next.state)) {
            setRunning(null);
            onDone();
          } else {
            setRunning(next);
          }
        },
        (caught: unknown) => {
          setError(describeError(caught));
          setRunning(null);
        },
      );
    }, 1000);
    return () => {
      window.clearTimeout(timer);
    };
  }, [active, onDone]);
  const failed =
    latest && isTerminalRun(latest.state) && latest.state !== "SUCCEEDED" ? latest : null;
  return (
    <section className="card stack" aria-labelledby="advisor-title" data-testid="advisor">
      <div className="card-head">
        <div>
          <h2 id="advisor-title" className="card-title">
            <Icon name="sparkles" size={16} /> Improvement plan
          </h2>
          <p className="card-sub">
            An ordered plan for the checkpoints that need work. Optional: the checkpoints come from
            the tools and do not need AI.
          </p>
        </div>
        {data.advisor.can_request ? (
          <button
            type="button"
            className="btn btn-ghost"
            disabled={active !== null}
            data-testid="advisor-request"
            onClick={() => {
              setError(null);
              requestPlan(projectId).then(setRunning, (caught: unknown) => {
                setError(describeError(caught));
              });
            }}
          >
            <Icon name="sparkles" size={15} />{" "}
            {active ? "Preparing the plan…" : latest ? "Update the plan" : "Create a plan with AI"}
          </button>
        ) : null}
      </div>
      {!data.advisor.enabled || (!data.advisor.can_request && data.advisor.reason) ? (
        <p className="small muted">{data.advisor.reason}</p>
      ) : null}
      {active ? (
        <p className="small" role="status">
          The advisor is reading the evidence…
        </p>
      ) : null}
      {failed ? (
        <Alert tone="warn">
          The last plan did not finish ({failed.error_message ?? failed.state}).
        </Alert>
      ) : null}
      {latest?.plan && !active ? <PlanView run={latest} data={data} /> : null}
      {error ? <Alert tone="bad">{error}</Alert> : null}
    </section>
  );
}

function HandledEditor({
  projectId,
  checkpoint,
  onSaved,
}: {
  projectId: string;
  checkpoint: Checkpoint;
  onSaved: (next: Insights) => void;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = (action: Promise<Insights>) => {
    setBusy(true);
    setError(null);
    action.then(
      (next) => {
        setBusy(false);
        setOpen(false);
        onSaved(next);
      },
      (caught: unknown) => {
        setBusy(false);
        setError(describeError(caught));
      },
    );
  };
  if (checkpoint.status === "handled") {
    return (
      <div className="stack stack-xs">
        <p className="small">
          <strong>Your team:</strong> {checkpoint.handled_reason}
        </p>
        <div className="row">
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            disabled={busy}
            onClick={() => {
              run(clearHandled(projectId, checkpoint.id));
            }}
          >
            Undo
          </button>
        </div>
        {error ? <Alert tone="bad">{error}</Alert> : null}
      </div>
    );
  }
  if (!open) {
    return (
      <button
        type="button"
        className="btn btn-ghost"
        data-testid="checkpoint-mark-handled"
        onClick={() => {
          setOpen(true);
        }}
      >
        Handled outside this code?
      </button>
    );
  }
  const id = `handled-${checkpoint.id}`;
  return (
    <div className="stack stack-sm" data-testid="checkpoint-handled-form">
      <div className="field">
        <label htmlFor={id}>How is it handled?</label>
        <input
          id={id}
          type="text"
          maxLength={500}
          value={reason}
          placeholder="For example: metrics and alerts come from the platform's monitoring agent"
          onChange={(event) => {
            setReason(event.target.value);
          }}
        />
      </div>
      <div className="row">
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy || reason.trim().length < 3}
          onClick={() => {
            run(markHandled(projectId, checkpoint.id, reason));
          }}
        >
          <Icon name="check" size={15} /> {busy ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={() => {
            setOpen(false);
          }}
        >
          Cancel
        </button>
        <span className="hint">Saved with your name and shown as your team&apos;s statement.</span>
      </div>
      {error ? <Alert tone="bad">{error}</Alert> : null}
    </div>
  );
}

function IssueList({ checkpoint }: { checkpoint: Checkpoint }) {
  return (
    <Disclosure summary={`The ${plural(checkpoint.issue_count, "issue")} behind it`}>
      <ul className="stack stack-xs plain-list">
        {checkpoint.issues.map((issue) => (
          <li key={issue.id} className="small">
            <SeverityChip severity={issue.severity} /> {issue.title}{" "}
            <span className="mono secondary wrap-anywhere">{issue.path}</span>
          </li>
        ))}
        {checkpoint.issue_count > checkpoint.issues.length ? (
          <li className="small muted">
            … and {plural(checkpoint.issue_count - checkpoint.issues.length, "more")}
          </li>
        ) : null}
      </ul>
    </Disclosure>
  );
}

function Steps({ checkpoint }: { checkpoint: Checkpoint }) {
  return (
    <div className="stack stack-xs">
      <h4 className="small">
        <strong>How to resolve it</strong>
      </h4>
      <ol className="stack stack-xs small">
        {checkpoint.steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
    </div>
  );
}

/** A checkpoint that needs work: what was found, why it matters, how to resolve it. */
function CheckpointCard({
  checkpoint,
  data,
  projectId,
  onChanged,
}: {
  checkpoint: Checkpoint;
  data: Insights;
  projectId: string;
  onChanged: (next: Insights) => void;
}) {
  const priority = PRIORITY[checkpoint.priority ?? "low"] ?? LOW_PRIORITY;
  return (
    <li
      className="card stack"
      data-testid={`checkpoint-${checkpoint.id}`}
      data-status={checkpoint.status}
      data-priority={checkpoint.priority ?? ""}
    >
      <div className="row">
        <StatusChip status={checkpoint.status} />
        <Chip tone={priority.tone}>{priority.label}</Chip>
        <Chip tone="neutral">{areaName(data, checkpoint.area)}</Chip>
      </div>
      <div className="stack stack-xs">
        <h3 className="subheading">{checkpoint.title}</h3>
        <p className="small">{checkpoint.summary}</p>
        <p className="small secondary">{checkpoint.why}</p>
      </div>
      <Steps checkpoint={checkpoint} />
      {checkpoint.issues.length > 0 ? <IssueList checkpoint={checkpoint} /> : null}
      <div className="row">
        {checkpoint.status === "attention" ? (
          <StartFixing projectId={projectId} data={data} insight={checkpoint} />
        ) : null}
        {checkpoint.can_mark_handled && data.can_edit ? (
          <HandledEditor projectId={projectId} checkpoint={checkpoint} onSaved={onChanged} />
        ) : null}
      </div>
    </li>
  );
}

/** A checkpoint in its area's list: the status first, the details on demand. */
function CheckpointRow({
  checkpoint,
  projectId,
  canEdit,
  onChanged,
}: {
  checkpoint: Checkpoint;
  projectId: string;
  canEdit: boolean;
  onChanged: (next: Insights) => void;
}) {
  return (
    <li data-testid={`checkpoint-${checkpoint.id}`} data-status={checkpoint.status}>
      <Disclosure
        summary={
          <span className="row">
            <StatusChip status={checkpoint.status} />
            <span className="small">{checkpoint.title}</span>
          </span>
        }
      >
        <div className="stack stack-sm">
          <p className="small">{checkpoint.summary}</p>
          {checkpoint.evidence.length > 0 ? (
            <div className="stack stack-xs">
              <h4 className="small">
                <strong>What the upload shows</strong>
              </h4>
              <EvidenceList items={checkpoint.evidence} testId="checkpoint-evidence" />
            </div>
          ) : null}
          {checkpoint.status === "handled" && canEdit ? (
            <HandledEditor projectId={projectId} checkpoint={checkpoint} onSaved={onChanged} />
          ) : checkpoint.status === "handled" ? (
            <p className="small">
              <strong>Your team:</strong> {checkpoint.handled_reason}
            </p>
          ) : null}
          {checkpoint.status !== "not_applicable" ? (
            <p className="small secondary">{checkpoint.why}</p>
          ) : null}
        </div>
      </Disclosure>
    </li>
  );
}

function AreaChecks({
  data,
  projectId,
  onChanged,
}: {
  data: Insights;
  projectId: string;
  onChanged: (next: Insights) => void;
}) {
  return (
    <section className="card stack" aria-labelledby="all-title" data-testid="all-checkpoints">
      <div>
        <h2 id="all-title" className="card-title">
          <Icon name="check" size={16} /> All checkpoints by area
        </h2>
        <p className="card-sub">
          &quot;In place&quot; and &quot;No issues found&quot; describe the uploaded code and
          configuration, not how the system behaves at run time.
        </p>
      </div>
      {data.areas.map((area) => {
        const mine = data.checkpoints.filter((c) => c.area === area.id);
        const shown = mine.filter((c) => c.status !== "not_applicable");
        const idle = mine.filter((c) => c.status === "not_applicable");
        return (
          <div
            key={area.id}
            id={`area-${area.id}`}
            className="stack stack-xs"
            data-testid={`area-${area.id}`}
          >
            <h3 className="subheading">{area.name}</h3>
            {shown.length > 0 ? (
              <ul className="stack stack-xs plain-list">
                {shown.map((checkpoint) => (
                  <CheckpointRow
                    key={checkpoint.id}
                    checkpoint={checkpoint}
                    projectId={projectId}
                    canEdit={data.can_edit}
                    onChanged={onChanged}
                  />
                ))}
              </ul>
            ) : null}
            {idle.length > 0 ? (
              <p className="small muted">
                Not applicable here: {idle.map((c) => c.title).join("; ")}.
              </p>
            ) : null}
          </div>
        );
      })}
      {data.not_checked.length > 0 ? (
        <div className="stack stack-xs" data-testid="not-checked">
          <h3 className="subheading">In the upload but not checked by refactorX</h3>
          <EvidenceList items={data.not_checked} testId="not-checked-items" />
        </div>
      ) : null}
    </section>
  );
}

function Checkpoints({
  data,
  projectId,
  snapshotId,
  onRefresh,
  onChanged,
}: {
  data: Insights;
  projectId: string;
  snapshotId: string | null;
  onRefresh: () => void;
  onChanged: (next: Insights) => void;
}) {
  const failing = data.checkpoints.filter((c) => FAILING.has(c.status));
  return (
    <div className="stack">
      <section className="card stack" aria-labelledby="areas-title">
        <div>
          <h2 id="areas-title" className="card-title">
            <Icon name="shield" size={16} /> NFR health by area
          </h2>
          <p className="card-sub">
            {data.basis
              ? `What the latest review${data.basis.reviewed_at ? ` (${formatRelative(data.basis.reviewed_at)})` : ""} shows for security, reliability, performance, operations, architecture and experience.`
              : "Review an upload to see its NFR checkpoints."}
          </p>
        </div>
        <AreaTiles data={data} />
      </section>
      {failing.length > 0 ? (
        <AdvisorCard data={data} projectId={projectId} onDone={onRefresh} />
      ) : null}
      {failing.length > 0 ? (
        <section className="stack" aria-labelledby="work-title">
          <h2 id="work-title" className="card-title">
            {plural(failing.length, "checkpoint")} to resolve
          </h2>
          <ul className="stack plain-list" data-testid="needs-work">
            {failing.map((checkpoint) => (
              <CheckpointCard
                key={checkpoint.id}
                checkpoint={checkpoint}
                data={data}
                projectId={projectId}
                onChanged={onChanged}
              />
            ))}
          </ul>
        </section>
      ) : (
        <Empty title={data.basis ? "Nothing to resolve" : "No review yet"}>
          <p className="small">
            {data.basis
              ? "No checkpoint needs work in the latest review."
              : "Upload code and review it to see its NFR checkpoints."}
          </p>
        </Empty>
      )}
      {data.basis ? <AreaChecks data={data} projectId={projectId} onChanged={onChanged} /> : null}
      <AiQuestions projectId={projectId} snapshotId={snapshotId} />
    </div>
  );
}

const VIEWS = [
  { id: "checkpoints", label: "NFR checkpoints" },
  { id: "architecture", label: "Architecture" },
];

export function InsightsView({
  projectId,
  snapshotId,
  view,
}: {
  projectId: string;
  snapshotId: string | null;
  view: string | null;
}) {
  const current = view === "architecture" ? "architecture" : "checkpoints";
  const [stamp, setStamp] = useState(0);
  const [saved, setSaved] = useState<Insights | null>(null);
  const insights = useAsync((signal) => fetchInsights(projectId, signal), [projectId, stamp]);
  const data = saved ?? insights.data;
  return (
    <div className="stack" data-testid="insights">
      <nav className="segmented" aria-label="Insights views">
        {VIEWS.map((item) => (
          <a
            key={item.id}
            href={`#/projects/${projectId}?tab=insights${item.id === "checkpoints" ? "" : `&view=${item.id}`}`}
            aria-current={current === item.id ? "page" : undefined}
            className="pill-toggle"
          >
            {item.label}
          </a>
        ))}
      </nav>
      {current === "checkpoints" ? (
        insights.error ? (
          <Alert tone="bad">{insights.error}</Alert>
        ) : data ? (
          <Checkpoints
            data={data}
            projectId={projectId}
            snapshotId={snapshotId}
            onRefresh={() => {
              setSaved(null);
              setStamp((n) => n + 1);
            }}
            onChanged={setSaved}
          />
        ) : (
          <Loading />
        )
      ) : null}
      {current === "architecture" ? (
        <div className="stack">
          <ArchitectureRules projectId={projectId} snapshotId={snapshotId} />
          {snapshotId ? (
            <ArchitectureView snapshotId={snapshotId} />
          ) : (
            <Empty title="No code uploaded yet">
              <p>Upload code and review it to see its architecture.</p>
            </Empty>
          )}
        </div>
      ) : null}
    </div>
  );
}

/** Overview: NFR health by area and the next checkpoints to resolve. */
export function HealthSummary({ projectId }: { projectId: string }) {
  const insights = useAsync((signal) => fetchInsights(projectId, signal), [projectId]);
  if (insights.error) return <Alert tone="bad">{insights.error}</Alert>;
  if (!insights.data) return <Loading />;
  const data = insights.data;
  const next = data.checkpoints.filter((c) => FAILING.has(c.status)).slice(0, 3);
  return (
    <section className="card stack" aria-labelledby="health-title" data-testid="health-summary">
      <div className="card-head">
        <h2 id="health-title" className="card-title">
          <Icon name="shield" size={16} /> NFR health and next steps
        </h2>
        <a className="btn btn-ghost btn-sm" href={`#/projects/${projectId}?tab=insights`}>
          All checkpoints <Icon name="arrow" size={14} />
        </a>
      </div>
      <AreaList data={data} />
      {next.length > 0 ? (
        <ol className="stack stack-xs" data-testid="next-steps">
          {next.map((checkpoint) => (
            <li key={checkpoint.id} className="small">
              <strong>{checkpoint.title}</strong>{" "}
              <span className="secondary">— {checkpoint.summary}</span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="small muted">
          {data.basis
            ? "No checkpoint needs work in the latest review."
            : "Review an upload to see its NFR checkpoints."}
        </p>
      )}
    </section>
  );
}
