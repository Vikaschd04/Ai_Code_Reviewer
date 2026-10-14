import { useState, type SubmitEvent } from "react";

import { describeError, type AiPolicy, type AiRun, type AiStatus } from "../api/client";
import {
  fetchAiPolicy,
  fetchAiStatus,
  listAiRuns,
  startAiRun,
  updateAiPolicy,
} from "../api/endpoints";
import { providerName, runTitle } from "../components/Ai";
import { Alert, Disclosure, Empty, Loading } from "../components/Common";
import { Icon } from "../components/Icon";
import { StatusBadge } from "../components/Status";
import { formatNumber, formatRelative } from "../lib/format";
import { navigate } from "../lib/router";
import { useSession } from "../lib/session";
import { useAsync } from "../lib/useAsync";

function NotSetUp({ status }: { status: AiStatus }) {
  const { principal } = useSession();
  return (
    <section className="card" data-testid="ai-not-set-up">
      <Empty title="AI review is not set up on this server">
        <p className="secondary">
          Automatic reviews work without it.
          {principal?.is_operator
            ? ""
            : " Ask an administrator if you need AI answers about your code."}
        </p>
        {status.admin_hint ? (
          <Disclosure summary="How to set it up">
            <p className="small secondary">{status.admin_hint}</p>
          </Disclosure>
        ) : null}
      </Empty>
    </section>
  );
}

type Connection = AiStatus["connections"][number];

/** The AI models an admin may choose for the project, with how each handles code. */
function ConnectionChoice({
  connections,
  value,
  onChange,
}: {
  connections: Connection[];
  value: string;
  onChange: (id: Connection["id"]) => void;
}) {
  return (
    <fieldset className="choice-group stack stack-xs" data-testid="ai-connection-choice">
      <legend className="small">
        <strong>AI model for this project</strong>
      </legend>
      {connections.map((connection) => (
        <label key={connection.id} className="checkbox-row small" data-connection={connection.id}>
          <input
            type="radio"
            name="ai-connection"
            checked={value === connection.id}
            onChange={() => {
              onChange(connection.id);
            }}
          />
          <span className="stack stack-xs">
            <span>
              {connection.label}{" "}
              {connection.free ? <span className="badge badge-neutral">Free</span> : null}
            </span>
            {connection.data_note ? (
              <span className="secondary">{connection.data_note}</span>
            ) : null}
          </span>
        </label>
      ))}
    </fieldset>
  );
}

function PolicyCard({
  status,
  policy,
  onChange,
}: {
  status: AiStatus;
  policy: AiPolicy;
  onChange: (next: AiPolicy) => void;
}) {
  const usable = status.connections.filter((c) => c.available);
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [choice, setChoice] = useState<Connection["id"]>(
    usable.some((c) => c.id === policy.connection)
      ? policy.connection
      : (usable[0]?.id ?? policy.connection),
  );
  const [changing, setChanging] = useState(false);
  const chosen = usable.find((c) => c.id === choice);
  const provider = chosen?.label ?? providerName(status);

  function save(enabled: boolean, connection?: Connection["id"]) {
    setBusy(true);
    setError(null);
    updateAiPolicy(policy, enabled, connection).then(
      (next) => {
        setBusy(false);
        setAgreed(false);
        setChanging(false);
        onChange(next);
      },
      (caught: unknown) => {
        setError(describeError(caught));
        setBusy(false);
      },
    );
  }

  const consent = (
    <>
      {usable.length > 1 ? (
        <ConnectionChoice
          connections={usable}
          value={choice}
          onChange={(id) => {
            setChoice(id);
            setAgreed(false);
          }}
        />
      ) : chosen?.data_note ? (
        <p className="small secondary">{chosen.data_note}</p>
      ) : null}
      <label className="checkbox-row">
        <input
          type="checkbox"
          checked={agreed}
          onChange={(event) => {
            setAgreed(event.target.checked);
          }}
        />
        I am allowed to share this project's code with {provider}.
      </label>
    </>
  );

  if (policy.enabled) {
    return (
      <section className="card stack" data-testid="ai-policy" data-enabled="true">
        <div className="row row-between">
          <div className="row">
            <span className="badge badge-ok">
              <Icon name="check" size={13} /> AI review is on
            </span>
            <span className="small secondary" data-testid="ai-policy-connection">
              Masked excerpts of this project's code go to {policy.connection_label} only when
              someone asks.
            </span>
          </div>
          {policy.can_edit ? (
            <div className="row">
              {usable.length > 1 && !changing ? (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => {
                    setChanging(true);
                  }}
                >
                  Change model
                </button>
              ) : null}
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                disabled={busy}
                onClick={() => {
                  save(false);
                }}
              >
                Switch off
              </button>
            </div>
          ) : null}
        </div>
        {changing ? (
          <div className="stack stack-sm">
            {consent}
            <div className="row">
              <button
                type="button"
                className="btn btn-primary"
                disabled={!agreed || busy || choice === policy.connection}
                onClick={() => {
                  save(true, choice);
                }}
              >
                Use this model
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => {
                  setChanging(false);
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        ) : null}
        {error ? <Alert tone="bad">{error}</Alert> : null}
      </section>
    );
  }
  return (
    <section
      className="card stack"
      aria-labelledby="ai-policy-title"
      data-testid="ai-policy"
      data-enabled="false"
    >
      <div className="stack stack-xs">
        <h2 id="ai-policy-title" className="card-title">
          <Icon name="lock" size={16} /> AI review is off for this project
        </h2>
        <p className="card-sub">
          Switching it on lets refactorX send short excerpts of this project's code to the AI model
          you choose when someone asks a question, plans improvements or asks for a fix, and never
          otherwise. Values that look like passwords or keys are masked first, every request is
          logged, and answers are checked against your code.
        </p>
      </div>
      {policy.can_edit ? (
        <>
          {consent}
          <div className="row">
            <button
              type="button"
              className="btn btn-primary"
              disabled={!agreed || busy}
              onClick={() => {
                save(true, choice);
              }}
            >
              <Icon name="sparkles" size={16} /> Switch on AI review
            </button>
          </div>
        </>
      ) : (
        <p className="small muted">A workspace admin can switch it on.</p>
      )}
      {error ? <Alert tone="bad">{error}</Alert> : null}
    </section>
  );
}

function AskCard({ projectId }: { projectId: string }) {
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = question.trim();
    if (text.length < 3) {
      setError("Type a question.");
      return;
    }
    setBusy(true);
    setError(null);
    startAiRun(projectId, { kind: "question", question: text }).then(
      (run) => {
        navigate(`#/ai-runs/${run.id}`);
      },
      (caught: unknown) => {
        setError(describeError(caught));
        setBusy(false);
      },
    );
  }

  return (
    <section className="card stack" aria-labelledby="ai-ask-title">
      <div className="stack stack-xs">
        <h2 id="ai-ask-title" className="card-title">
          <Icon name="sparkles" size={16} /> Ask about this code
        </h2>
        <p className="card-sub">Answers point to the exact lines they rely on.</p>
      </div>
      <form className="form-grid" onSubmit={submit}>
        <label>
          <span className="visually-hidden">Your question</span>
          <textarea
            value={question}
            rows={4}
            maxLength={2000}
            placeholder="For example: where is the order total calculated, and is the discount applied correctly?"
            onChange={(event) => {
              setQuestion(event.target.value);
            }}
          />
        </label>
        <div className="row">
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Starting…" : "Ask"}
          </button>
        </div>
      </form>
      {error ? <Alert tone="bad">{error}</Alert> : null}
    </section>
  );
}

function RunsCard({ runs, status }: { runs: AiRun[]; status: AiStatus }) {
  const limit = status.month.token_limit;
  const share = limit > 0 ? Math.min(100, Math.round((status.month.tokens / limit) * 100)) : null;
  return (
    <section className="card stack" aria-labelledby="ai-runs-title">
      <div className="card-head">
        <h2 id="ai-runs-title" className="card-title">
          Earlier questions and reviews
        </h2>
        {share !== null ? (
          <span className="small muted" title={`${formatNumber(status.month.tokens)} tokens`}>
            {share}% of this month's AI allowance used
          </span>
        ) : null}
      </div>
      {runs.length === 0 ? (
        <p className="small muted">Nothing yet.</p>
      ) : (
        <div className="table-wrap">
          <table className="data-table" data-testid="ai-runs">
            <caption className="visually-hidden">AI questions and reviews</caption>
            <thead>
              <tr>
                <th scope="col">Request</th>
                <th scope="col">Result</th>
                <th scope="col">Asked</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id}>
                  <th scope="row" className="ai-run-title">
                    <a href={`#/ai-runs/${run.id}`}>{runTitle(run)}</a>
                  </th>
                  <td>
                    <StatusBadge state={run.state} />
                  </td>
                  <td className="nowrap">{formatRelative(run.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/** Settings: whether AI may be used for this project (admins decide), or why it is not set up. */
export function AiSettings({ projectId }: { projectId: string }) {
  const status = useAsync((signal) => fetchAiStatus(signal), []);
  const loaded = useAsync((signal) => fetchAiPolicy(projectId, signal), [projectId]);
  const [policy, setPolicy] = useState<AiPolicy | null>(null);
  const current = policy ?? loaded.data;
  const error = status.error ?? loaded.error;
  if (error) return <Alert tone="bad">{error}</Alert>;
  if (!status.data || !current) return <Loading />;
  return status.data.available ? (
    <PolicyCard status={status.data} policy={current} onChange={setPolicy} />
  ) : (
    <NotSetUp status={status.data} />
  );
}

/** Insights: ask AI a question about the code, and earlier answers (only when AI is on). */
export function AiQuestions({
  projectId,
  snapshotId,
}: {
  projectId: string;
  snapshotId: string | null;
}) {
  const status = useAsync((signal) => fetchAiStatus(signal), []);
  const policy = useAsync((signal) => fetchAiPolicy(projectId, signal), [projectId]);
  const runs = useAsync((signal) => listAiRuns(projectId, signal), [projectId]);
  if (!status.data || !policy.data || !runs.data) return null;
  const answers = runs.data.filter((run) => run.kind !== "advisor" && run.kind !== "fix");
  const ready = status.data.available && policy.data.enabled && snapshotId !== null;
  if (!ready && answers.length === 0) return null;
  return (
    <div className="stack">
      {ready ? <AskCard projectId={projectId} /> : null}
      {answers.length > 0 ? <RunsCard runs={answers} status={status.data} /> : null}
    </div>
  );
}
