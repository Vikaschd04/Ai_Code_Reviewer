/** Insights (docs/PRODUCT_SIMPLIFICATION.md): what to improve, by area. The tools' recommendations
 * come first; the AI improvement plan orders and explains them, with every step checked against
 * the same evidence. The NFR questionnaire and the architecture are views of the same tab. */
import { useEffect, useState } from "react";

import { describeError, type AiRun, type Insight, type Insights } from "../api/client";
import {
  createWorkspace,
  fetchAiRun,
  fetchInsights,
  listWorkspaces,
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
import { NfrView } from "./NfrView";

const UNKNOWN_STATE = { label: "Not enough evidence", tone: "neutral", icon: "info" as IconName };
const AREA_STATE: Record<string, { label: string; tone: string; icon: IconName }> = {
  attention: { label: "Needs attention", tone: "warn", icon: "alert" },
  improve: { label: "Could be better", tone: "live", icon: "info" },
  no_problems: { label: "No problems found", tone: "ok", icon: "check" },
  unknown: { label: "Not enough evidence", tone: "neutral", icon: "info" },
};
const LOW_PRIORITY = { label: "Low priority", tone: "neutral" };
const PRIORITY: Record<string, { label: string; tone: string }> = {
  high: { label: "High priority", tone: "warn" },
  medium: { label: "Medium priority", tone: "live" },
  low: { label: "Low priority", tone: "neutral" },
};
const EFFORT: Record<string, string> = { small: "Small", medium: "Medium", large: "Large" };

function Chip({ tone, icon, children }: { tone: string; icon?: IconName; children: string }) {
  return (
    <span className={`badge badge-${tone}`}>
      {icon ? <Icon name={icon} size={12} /> : null} {children}
    </span>
  );
}

function areaName(data: Insights, id: string): string {
  return data.areas.find((a) => a.id === id)?.name ?? id;
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
        const count =
          (area.insights.high ?? 0) + (area.insights.medium ?? 0) + (area.insights.low ?? 0);
        return (
          <div key={area.id} className="tile" data-area={area.id} data-state={area.state}>
            <span className="small">
              <strong>{area.name}</strong>
            </span>
            <span className="tile-label">
              <Chip tone={look.tone} icon={look.icon}>
                {look.label}
              </Chip>
            </span>
            <span className="small muted">
              {count > 0 ? plural(count, "recommendation") : "No recommendations"}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function StartFixing({
  projectId,
  data,
  insight,
}: {
  projectId: string;
  data: Insights;
  insight: Insight;
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
        data-testid="insight-start-fixing"
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

function RecommendationCard({
  insight,
  data,
  projectId,
}: {
  insight: Insight;
  data: Insights;
  projectId: string;
}) {
  const priority = PRIORITY[insight.priority] ?? LOW_PRIORITY;
  return (
    <li
      className="card stack"
      data-testid={`insight-${insight.id}`}
      data-priority={insight.priority}
    >
      <div className="row">
        <Chip tone={priority.tone}>{priority.label}</Chip>
        <Chip tone="neutral">{areaName(data, insight.area)}</Chip>
      </div>
      <div className="stack stack-xs">
        <h3 className="subheading">{insight.title}</h3>
        <p className="small">{insight.summary}</p>
        <p className="small secondary">{insight.why}</p>
      </div>
      <div className="stack stack-xs">
        <h4 className="small">
          <strong>How to improve it</strong>
        </h4>
        <ol className="stack stack-xs small">
          {insight.steps.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
      </div>
      {insight.issues.length > 0 ? (
        <Disclosure summary={`The ${plural(insight.issue_count, "issue")} behind it`}>
          <ul className="stack stack-xs plain-list">
            {insight.issues.map((issue) => (
              <li key={issue.id} className="small">
                <SeverityChip severity={issue.severity} /> {issue.title}{" "}
                <span className="mono secondary wrap-anywhere">{issue.path}</span>
              </li>
            ))}
            {insight.issue_count > insight.issues.length ? (
              <li className="small muted">
                … and {plural(insight.issue_count - insight.issues.length, "more")}
              </li>
            ) : null}
          </ul>
        </Disclosure>
      ) : null}
      <div className="row">
        {insight.kind === "issues" ? (
          <StartFixing projectId={projectId} data={data} insight={insight} />
        ) : (
          <a className="btn btn-ghost" href={`#/projects/${projectId}?tab=insights&view=nfr`}>
            {insight.kind === "targets" ? "Enter your targets" : "Handled elsewhere? Say so"}{" "}
            <Icon name="arrow" size={14} />
          </a>
        )}
      </div>
    </li>
  );
}

function TargetsCard({ data, projectId }: { data: Insights; projectId: string }) {
  const requests = data.recommendations.filter((r) => r.kind === "targets");
  if (requests.length === 0) return null;
  return (
    <li className="card stack" data-testid="insight-targets">
      <div className="stack stack-xs">
        <h3 className="subheading">Tell us your targets</h3>
        <p className="small secondary">
          Evidence in code shows intent; only your targets say whether it is enough.
        </p>
      </div>
      <ul className="stack stack-xs plain-list">
        {requests.map((request) => (
          <li key={request.id} className="small">
            <strong>{areaName(data, request.area)}:</strong>{" "}
            {request.summary.replace("Needed to judge this area: ", "")}
          </li>
        ))}
      </ul>
      <div className="row">
        <a className="btn btn-ghost" href={`#/projects/${projectId}?tab=insights&view=nfr`}>
          Enter your targets <Icon name="arrow" size={14} />
        </a>
      </div>
    </li>
  );
}

function PlanView({ run, data }: { run: AiRun; data: Insights }) {
  const plan = run.plan;
  if (!plan) return null;
  const titles = new Map(data.recommendations.map((r) => [r.id, r.title]));
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
        was checked against the recommendations, the tools&apos; facts and the cited code;
        priorities and counts come from the tools, never from AI.
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
            An ordered plan built from the recommendations below. Optional: the recommendations come
            from the tools and do not need AI.
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

function Recommendations({
  data,
  projectId,
  snapshotId,
  onRefresh,
}: {
  data: Insights;
  projectId: string;
  snapshotId: string | null;
  onRefresh: () => void;
}) {
  return (
    <div className="stack">
      <section className="card stack" aria-labelledby="areas-title">
        <div>
          <h2 id="areas-title" className="card-title">
            <Icon name="shield" size={16} /> Health by area
          </h2>
          <p className="card-sub">
            {data.basis
              ? `From the latest review${data.basis.reviewed_at ? ` (${formatRelative(data.basis.reviewed_at)})` : ""}, your issues and your NFR answers.`
              : "Review an upload to see what the code shows."}
          </p>
        </div>
        <AreaTiles data={data} />
      </section>
      <AdvisorCard data={data} projectId={projectId} onDone={onRefresh} />
      {data.recommendations.length > 0 ? (
        <ul className="stack plain-list" data-testid="recommendations">
          {data.recommendations
            .filter((insight) => insight.kind !== "targets")
            .map((insight) => (
              <RecommendationCard
                key={insight.id}
                insight={insight}
                data={data}
                projectId={projectId}
              />
            ))}
          <TargetsCard data={data} projectId={projectId} />
        </ul>
      ) : (
        <Empty title="No recommendations">
          <p className="small">
            {data.basis
              ? "Nothing the tools can recommend from this review."
              : "Upload code and review it to get recommendations."}
          </p>
        </Empty>
      )}
      <AiQuestions projectId={projectId} snapshotId={snapshotId} />
    </div>
  );
}

const VIEWS = [
  { id: "recommendations", label: "Recommendations" },
  { id: "nfr", label: "NFR questionnaire" },
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
  const current = VIEWS.some((v) => v.id === view)
    ? (view ?? "recommendations")
    : "recommendations";
  const [stamp, setStamp] = useState(0);
  const insights = useAsync((signal) => fetchInsights(projectId, signal), [projectId, stamp]);
  return (
    <div className="stack" data-testid="insights">
      <nav className="segmented" aria-label="Insights views">
        {VIEWS.map((item) => (
          <a
            key={item.id}
            href={`#/projects/${projectId}?tab=insights${item.id === "recommendations" ? "" : `&view=${item.id}`}`}
            aria-current={current === item.id ? "page" : undefined}
            className="pill-toggle"
          >
            {item.label}
          </a>
        ))}
      </nav>
      {current === "recommendations" ? (
        insights.error ? (
          <Alert tone="bad">{insights.error}</Alert>
        ) : insights.data ? (
          <Recommendations
            data={insights.data}
            projectId={projectId}
            snapshotId={snapshotId}
            onRefresh={() => {
              setStamp((n) => n + 1);
            }}
          />
        ) : (
          <Loading />
        )
      ) : null}
      {current === "nfr" ? <NfrView projectId={projectId} /> : null}
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

/** Overview: health by area and the next steps, from the same insights. */
export function HealthSummary({ projectId }: { projectId: string }) {
  const insights = useAsync((signal) => fetchInsights(projectId, signal), [projectId]);
  if (insights.error) return <Alert tone="bad">{insights.error}</Alert>;
  if (!insights.data) return <Loading />;
  const data = insights.data;
  const next = data.recommendations.filter((r) => r.kind !== "targets").slice(0, 3);
  return (
    <section className="card stack" aria-labelledby="health-title" data-testid="health-summary">
      <div className="card-head">
        <h2 id="health-title" className="card-title">
          <Icon name="shield" size={16} /> Health and next steps
        </h2>
        <a className="btn btn-ghost btn-sm" href={`#/projects/${projectId}?tab=insights`}>
          All insights <Icon name="arrow" size={14} />
        </a>
      </div>
      <AreaList data={data} />
      {next.length > 0 ? (
        <ol className="stack stack-xs" data-testid="next-steps">
          {next.map((insight) => (
            <li key={insight.id} className="small">
              <strong>{insight.title}</strong>{" "}
              <span className="secondary">— {insight.summary}</span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="small muted">
          {data.basis
            ? "No recommendations from the latest review."
            : "Review an upload to get recommendations."}
        </p>
      )}
    </section>
  );
}
