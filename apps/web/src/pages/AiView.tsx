import { useState, type SubmitEvent } from "react";

import {
  describeError,
  type AiCatalog,
  type AiPolicy,
  type AiRun,
  type AiStatus,
} from "../api/client";
import {
  deleteProjectAiModel,
  fetchAiCatalog,
  fetchAiPolicy,
  fetchAiStatus,
  fetchProjectAiModel,
  listAiRuns,
  saveProjectAiModel,
  startAiRun,
  updateAiPolicy,
} from "../api/endpoints";
import { runTitle } from "../components/Ai";
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
    usable.find((c) => c.id === policy.connection)?.id ?? usable[0]?.id ?? "primary",
  );
  const [changing, setChanging] = useState(false);
  const chosen = usable.find((c) => c.id === choice);
  const provider = chosen?.label ?? "the AI provider chosen for this project";

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
              {policy.ready
                ? `Masked excerpts of this project's code go to ${policy.connection_label} only when someone asks.`
                : "Choose the AI model and add your API key below to start."}
            </span>
          </div>
          {policy.can_edit ? (
            <div className="row">
              {(usable.length > 1 || (usable.length > 0 && policy.connection === "project")) &&
              !changing ? (
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
                save(true, usable.length > 0 ? choice : undefined);
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

/** The project's own provider, model and API key (ADR 0026): tested, then stored encrypted. */
function ProjectModelCard({
  projectId,
  catalog,
  policy,
  onChange,
}: {
  projectId: string;
  catalog: AiCatalog;
  policy: AiPolicy;
  onChange: () => void;
}) {
  const [stamp, setStamp] = useState(0);
  const saved = useAsync((signal) => fetchProjectAiModel(projectId, signal), [projectId, stamp]);
  const [editing, setEditing] = useState(false);
  const firstProvider = catalog.providers[0];
  const [providerId, setProviderId] = useState<string>(firstProvider?.id ?? "anthropic");
  const provider = catalog.providers.find((p) => p.id === providerId) ?? firstProvider;
  const [modelId, setModelId] = useState<string>(provider?.models[0]?.id ?? "");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (saved.error) return <Alert tone="bad">{saved.error}</Alert>;
  if (!saved.data || !provider) return <Loading />;
  const current = saved.data;
  const done = () => {
    setBusy(false);
    setEditing(false);
    setApiKey("");
    setStamp((n) => n + 1);
    onChange();
  };
  const fail = (caught: unknown) => {
    setBusy(false);
    setError(describeError(caught));
  };

  const form = (
    <div className="stack stack-sm" data-testid="ai-model-form">
      <div className="grid grid-auto">
        <div className="field">
          <label htmlFor="ai-provider">Provider</label>
          <select
            id="ai-provider"
            value={provider.id}
            onChange={(event) => {
              const next = catalog.providers.find((p) => p.id === event.target.value);
              setProviderId(event.target.value);
              setModelId(next?.models[0]?.id ?? "");
              setError(null);
            }}
          >
            {catalog.providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {p.models.length > 0 && p.models.every((m) => m.free) ? " (free models)" : ""}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="ai-model">Model</label>
          <select
            id="ai-model"
            value={modelId}
            onChange={(event) => {
              setModelId(event.target.value);
              setError(null);
            }}
          >
            {provider.models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
                {m.free ? " — free" : ""}
                {m.note ? ` — ${m.note}` : ""}
              </option>
            ))}
          </select>
        </div>
      </div>
      <p className="small secondary">{provider.data_note}</p>
      <div className="field">
        <label htmlFor="ai-key">API key</label>
        <input
          id="ai-key"
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={apiKey}
          placeholder="Paste your API key"
          onChange={(event) => {
            setApiKey(event.target.value);
            setError(null);
          }}
        />
        <span className="hint">{provider.key_hint}</span>
      </div>
      <div className="row">
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy || apiKey.trim().length < 8 || !modelId}
          onClick={() => {
            setBusy(true);
            setError(null);
            saveProjectAiModel(projectId, provider.id, modelId, apiKey.trim()).then(done, fail);
          }}
        >
          <Icon name="check" size={15} /> {busy ? "Testing the key…" : "Test and save"}
        </button>
        {current.configured ? (
          <button
            type="button"
            className="btn btn-ghost"
            disabled={busy}
            onClick={() => {
              setEditing(false);
              setError(null);
            }}
          >
            Cancel
          </button>
        ) : null}
        <span className="hint">
          The key is tested first, stored encrypted, used only for this project and never shown
          again.
        </span>
      </div>
    </div>
  );

  return (
    <section
      className="card stack"
      aria-labelledby="ai-model-title"
      data-testid="ai-model"
      data-configured={current.configured ? "true" : "false"}
    >
      <div className="stack stack-xs">
        <h2 id="ai-model-title" className="card-title">
          <Icon name="sparkles" size={16} /> AI model and API key
        </h2>
        <p className="card-sub">
          Choose the model this project&apos;s AI uses for fixes, plans and questions, and add your
          own API key.
        </p>
      </div>
      {!current.can_store_keys ? (
        <Alert tone="warn">
          This server cannot store API keys yet. An administrator must set CRP_SECRETS_KEY.
        </Alert>
      ) : null}
      {current.configured && !editing ? (
        <div className="stack stack-sm" data-testid="ai-model-saved">
          <div className="row">
            <span className={`badge ${current.in_use ? "badge-ok" : "badge-neutral"}`}>
              <Icon name={current.in_use ? "check" : "info"} size={13} />{" "}
              {current.in_use ? "In use" : "Saved, not in use"}
            </span>
            <strong className="small">{current.model_label}</strong>
          </div>
          <p className="small secondary">
            Key ending {current.key_hint}
            {current.tested_at ? ` · tested ${formatRelative(current.tested_at)}` : ""}
          </p>
          {current.can_edit ? (
            <div className="row">
              {!current.in_use ? (
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  disabled={busy}
                  onClick={() => {
                    setBusy(true);
                    updateAiPolicy(policy, true, "project").then(done, fail);
                  }}
                >
                  Use this model
                </button>
              ) : null}
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                disabled={busy || !current.can_store_keys}
                onClick={() => {
                  const before = catalog.providers.find((p) => p.id === current.provider);
                  if (before) {
                    setProviderId(before.id);
                    setModelId(
                      before.models.find((m) => m.id === current.model)?.id ??
                        before.models[0]?.id ??
                        "",
                    );
                  }
                  setEditing(true);
                }}
              >
                Change model or key
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                disabled={busy}
                onClick={() => {
                  setBusy(true);
                  deleteProjectAiModel(projectId).then(done, fail);
                }}
              >
                Remove key
              </button>
            </div>
          ) : null}
        </div>
      ) : current.can_edit ? (
        current.can_store_keys ? (
          form
        ) : null
      ) : (
        <p className="small muted">A workspace admin can choose the model and add the key.</p>
      )}
      {error ? (
        <Alert tone="bad">
          <span data-testid="ai-model-error">{error}</span>
        </Alert>
      ) : null}
    </section>
  );
}

/** Settings: whether AI may be used for this project (admins decide), which model it uses
 * with whose key, or why it is not set up. */
export function AiSettings({ projectId }: { projectId: string }) {
  const status = useAsync((signal) => fetchAiStatus(signal), []);
  const catalog = useAsync((signal) => fetchAiCatalog(signal), []);
  const [stamp, setStamp] = useState(0);
  const loaded = useAsync((signal) => fetchAiPolicy(projectId, signal), [projectId, stamp]);
  const [policy, setPolicy] = useState<AiPolicy | null>(null);
  const current = policy ?? loaded.data;
  const error = status.error ?? loaded.error ?? catalog.error;
  if (error) return <Alert tone="bad">{error}</Alert>;
  if (!status.data || !current || !catalog.data) return <Loading />;
  if (!status.data.available && !catalog.data.can_store_keys) {
    return <NotSetUp status={status.data} />;
  }
  return (
    <div className="stack">
      <PolicyCard status={status.data} policy={current} onChange={setPolicy} />
      {current.enabled ? (
        <ProjectModelCard
          projectId={projectId}
          catalog={catalog.data}
          policy={current}
          onChange={() => {
            setPolicy(null);
            setStamp((n) => n + 1);
          }}
        />
      ) : null}
    </div>
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
  const ready = policy.data.ready && policy.data.enabled && snapshotId !== null;
  if (!ready && answers.length === 0) return null;
  return (
    <div className="stack">
      {ready ? <AskCard projectId={projectId} /> : null}
      {answers.length > 0 ? <RunsCard runs={answers} status={status.data} /> : null}
    </div>
  );
}
