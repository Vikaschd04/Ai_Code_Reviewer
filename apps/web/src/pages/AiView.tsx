import { useEffect, useState, type SubmitEvent } from "react";

import {
  describeError,
  type AiPolicy,
  type AiRun,
  type AiStatus,
  type FileEntry,
} from "../api/client";
import {
  fetchAiPolicy,
  fetchAiStatus,
  listAiRuns,
  listFiles,
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

const MAX_FILES = 5;

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

function PolicyCard({
  status,
  policy,
  onChange,
}: {
  status: AiStatus;
  policy: AiPolicy;
  onChange: (next: AiPolicy) => void;
}) {
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const provider = providerName(status);

  function save(enabled: boolean) {
    setBusy(true);
    setError(null);
    updateAiPolicy(policy, enabled).then(
      (next) => {
        setBusy(false);
        setAgreed(false);
        onChange(next);
      },
      (caught: unknown) => {
        setError(describeError(caught));
        setBusy(false);
      },
    );
  }

  if (policy.enabled) {
    return (
      <section className="card" data-testid="ai-policy" data-enabled="true">
        <div className="row row-between">
          <div className="row">
            <span className="badge badge-ok">
              <Icon name="check" size={13} /> AI review is on
            </span>
            <span className="small secondary">
              Masked excerpts of this project's code go to {provider} only when someone asks.
            </span>
          </div>
          {policy.can_edit ? (
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
          ) : null}
        </div>
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
          Switching it on lets refactorX send short excerpts of this project's code to {provider}{" "}
          when someone asks a question or starts an AI review, and never otherwise. Values that look
          like passwords or keys are masked first, every request is logged, and answers are checked
          against your code.
        </p>
      </div>
      {policy.can_edit ? (
        <>
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
          <div className="row">
            <button
              type="button"
              className="btn btn-primary"
              disabled={!agreed || busy}
              onClick={() => {
                save(true);
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

function ReviewFilesCard({ projectId, snapshotId }: { projectId: string; snapshotId: string }) {
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<FileEntry[]>([]);
  const [chosen, setChosen] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const text = query.trim();
    const timer = window.setTimeout(() => {
      if (!text) {
        setMatches([]);
        return;
      }
      listFiles(snapshotId, { disposition: "ANALYZABLE", q: text }, controller.signal).then(
        (page) => {
          setMatches(page.items.slice(0, 8));
        },
        () => {
          setMatches([]);
        },
      );
    }, 200);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [snapshotId, query]);

  function start() {
    setBusy(true);
    setError(null);
    startAiRun(projectId, { kind: "file_review", paths: chosen }).then(
      (run) => {
        navigate(`#/ai-runs/${run.id}`);
      },
      (caught: unknown) => {
        setError(describeError(caught));
        setBusy(false);
      },
    );
  }

  const full = chosen.length >= MAX_FILES;
  return (
    <section className="card stack" aria-labelledby="ai-files-title">
      <div className="stack stack-xs">
        <h2 id="ai-files-title" className="card-title">
          <Icon name="scan" size={16} /> Review files with AI
        </h2>
        <p className="card-sub">
          Look for bugs and risks the automatic checks may miss, in up to {MAX_FILES} files.
        </p>
      </div>
      {chosen.length > 0 ? (
        <ul className="chip-list" aria-label="Files to review">
          {chosen.map((path) => (
            <li key={path} className="chip mono">
              {path}
              <button
                type="button"
                className="icon-btn icon-btn-xs"
                aria-label={`Remove ${path}`}
                onClick={() => {
                  setChosen(chosen.filter((item) => item !== path));
                }}
              >
                <Icon name="x" size={12} />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <input
        type="search"
        value={query}
        placeholder="Find a file by name"
        aria-label="Find a file to review"
        disabled={full}
        onChange={(event) => {
          setQuery(event.target.value);
        }}
      />
      {!full && matches.length > 0 ? (
        <ul className="result-list" aria-label="Matching files">
          {matches
            .filter((file) => !chosen.includes(file.path))
            .map((file) => (
              <li key={file.id}>
                <button
                  type="button"
                  className="result-item"
                  onClick={() => {
                    setChosen([...chosen, file.path]);
                  }}
                >
                  <Icon name="file" size={14} />
                  <span className="mono truncate">{file.path}</span>
                </button>
              </li>
            ))}
        </ul>
      ) : null}
      <div className="row">
        <button
          type="button"
          className="btn btn-primary"
          disabled={chosen.length === 0 || busy}
          onClick={start}
        >
          {busy
            ? "Starting…"
            : chosen.length > 1
              ? `Review ${String(chosen.length)} files`
              : "Review file"}
        </button>
      </div>
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

/** Project tab: AI status, the project's sharing decision, ask/review forms and past runs. */
export function AiView({
  projectId,
  snapshotId,
}: {
  projectId: string;
  snapshotId: string | null;
}) {
  const status = useAsync((signal) => fetchAiStatus(signal), []);
  const loaded = useAsync((signal) => fetchAiPolicy(projectId, signal), [projectId]);
  const runs = useAsync((signal) => listAiRuns(projectId, signal), [projectId]);
  const [policy, setPolicy] = useState<AiPolicy | null>(null);
  const current = policy ?? loaded.data;
  const error = status.error ?? loaded.error ?? runs.error;
  if (error) return <Alert tone="bad">{error}</Alert>;
  if (!status.data || !current || !runs.data) return <Loading />;
  const ready = status.data.available && current.enabled;
  return (
    <div className="stack">
      {status.data.available ? (
        <PolicyCard status={status.data} policy={current} onChange={setPolicy} />
      ) : (
        <NotSetUp status={status.data} />
      )}
      {ready && snapshotId ? (
        <div className="split split-even">
          <AskCard projectId={projectId} />
          <ReviewFilesCard projectId={projectId} snapshotId={snapshotId} />
        </div>
      ) : null}
      {ready && !snapshotId ? (
        <Empty title="No code uploaded yet">
          <p>Upload code to ask AI about it.</p>
        </Empty>
      ) : null}
      {status.data.available || runs.data.length > 0 ? (
        <RunsCard runs={runs.data} status={status.data} />
      ) : null}
    </div>
  );
}
