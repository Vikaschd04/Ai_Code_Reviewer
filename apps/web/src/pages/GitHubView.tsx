import { useEffect, useState } from "react";

import {
  describeError,
  type CodeReview,
  type GitConnection,
  type GitConnectionUpdate,
} from "../api/client";
import {
  connectRepository,
  disconnectRepository,
  fetchGitConnection,
  fetchGitHubStatus,
  listCodeReviews,
  listGitInstallations,
  startCodeReview,
  updateGitConnection,
} from "../api/endpoints";
import { Alert, Disclosure, Empty, Loading } from "../components/Common";
import { REVIEW_ACTIVE, ReviewOutcome, ReviewTarget } from "../components/GitHub";
import { Icon } from "../components/Icon";
import { StatusBadge } from "../components/Status";
import { formatRelative } from "../lib/format";
import { navigate } from "../lib/router";
import { useAsync } from "../lib/useAsync";

const PUBLISHED: Record<string, string> = {
  published: "Posted to GitHub",
  failed: "Could not post to GitHub",
};

type Flag =
  | "review_pushes"
  | "review_pull_requests"
  | "review_forks"
  | "publish_checks"
  | "publish_pull_requests";

function ConnectCard({
  projectId,
  workspaceId,
  onDone,
}: {
  projectId: string;
  workspaceId: string;
  onDone: () => void;
}) {
  const installations = useAsync(
    (signal) => listGitInstallations(workspaceId, signal),
    [workspaceId],
  );
  const [choice, setChoice] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (installations.error) return <Alert tone="bad">{installations.error}</Alert>;
  if (!installations.data) return <Loading />;
  const repos = installations.data
    .filter((i) => !i.revoked && !i.suspended)
    .flatMap((i) => i.repositories)
    .filter((r) => !r.removed && !r.project_id);
  return (
    <section
      className="card stack"
      aria-labelledby="connect-repo"
      data-testid="github-connect-repo"
    >
      <div className="stack stack-xs">
        <h2 id="connect-repo" className="card-title">
          <Icon name="branch" size={16} /> Connect a GitHub repository
        </h2>
        <p className="card-sub">
          refactorX reviews the default branch right away and then every push and pull request.
          Nothing is posted to GitHub until you allow it below.
        </p>
      </div>
      {repos.length === 0 ? (
        <p className="small secondary">
          No repository is available. <a href="#/github">Link a GitHub account</a> or share more
          repositories with the refactorX app.
        </p>
      ) : (
        <form
          className="row"
          onSubmit={(event) => {
            event.preventDefault();
            if (!choice) return;
            setBusy(true);
            setError(null);
            connectRepository(projectId, choice).then(
              () => {
                setBusy(false);
                onDone();
              },
              (caught: unknown) => {
                setError(describeError(caught));
                setBusy(false);
              },
            );
          }}
        >
          <label className="grow">
            <span className="visually-hidden">Repository</span>
            <select
              value={choice}
              onChange={(event) => {
                setChoice(event.target.value);
              }}
            >
              <option value="">Choose a repository…</option>
              {repos.map((repo) => (
                <option key={repo.id} value={repo.id}>
                  {repo.full_name}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" className="btn btn-primary" disabled={!choice || busy}>
            {busy ? "Connecting…" : "Connect and review"}
          </button>
        </form>
      )}
      {error ? <Alert tone="bad">{error}</Alert> : null}
    </section>
  );
}

function RepositoryCard({
  projectId,
  connection,
  onChange,
}: {
  projectId: string;
  connection: GitConnection;
  onChange: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [number, setNumber] = useState("");
  const [confirming, setConfirming] = useState(false);
  const active = connection.status === "active";
  const repo = connection.repository;

  function review(pullRequest: number | null) {
    setBusy(true);
    setError(null);
    startCodeReview(
      projectId,
      pullRequest
        ? { kind: "pull_request", pull_request: pullRequest, full: false }
        : { kind: "branch", full: false },
    ).then(
      (started) => {
        navigate(`#/reviews/${started.id}`);
      },
      (caught: unknown) => {
        setError(describeError(caught));
        setBusy(false);
      },
    );
  }

  return (
    <section className="card stack" aria-labelledby="repo-title" data-testid="github-repository">
      <div className="card-head">
        <div className="stack stack-xs">
          <h2 id="repo-title" className="card-title">
            <Icon name="branch" size={16} /> <span className="mono">{repo?.full_name}</span>
          </h2>
          <p className="card-sub">
            Default branch <span className="mono">{repo?.default_branch}</span>
            {connection.installation_account ? ` · via ${connection.installation_account}` : ""}
          </p>
        </div>
        {active ? (
          <span className="badge badge-ok">
            <Icon name="check" size={13} /> Connected
          </span>
        ) : (
          <span className="badge badge-bad">
            <Icon name="x" size={13} /> Not available
          </span>
        )}
      </div>
      {connection.status_reason ? <Alert tone="warn">{connection.status_reason}</Alert> : null}
      {active ? (
        <div className="row">
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={busy}
            data-testid="review-branch"
            onClick={() => {
              review(null);
            }}
          >
            <Icon name="play" size={14} /> Review latest commit
          </button>
          <form
            className="row"
            onSubmit={(event) => {
              event.preventDefault();
              const value = Number.parseInt(number, 10);
              if (Number.isFinite(value) && value > 0) review(value);
            }}
          >
            <label>
              <span className="visually-hidden">Pull request number</span>
              <input
                className="input-narrow"
                inputMode="numeric"
                placeholder="PR #"
                value={number}
                onChange={(event) => {
                  setNumber(event.target.value.replace(/\D/g, ""));
                }}
              />
            </label>
            <button type="submit" className="btn btn-ghost btn-sm" disabled={busy || !number}>
              Review pull request
            </button>
          </form>
        </div>
      ) : null}
      {connection.can_edit ? (
        confirming ? (
          <Alert tone="warn">
            <span className="stack stack-sm">
              <span>
                Disconnecting stops reviews of this repository. Earlier reviews and results stay.
              </span>
              <span className="row">
                <button
                  type="button"
                  className="btn btn-danger btn-sm"
                  disabled={busy}
                  onClick={() => {
                    setBusy(true);
                    disconnectRepository(projectId).then(onChange, (caught: unknown) => {
                      setError(describeError(caught));
                      setBusy(false);
                    });
                  }}
                >
                  Disconnect
                </button>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => {
                    setConfirming(false);
                  }}
                >
                  Cancel
                </button>
              </span>
            </span>
          </Alert>
        ) : (
          <div className="row">
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => {
                setConfirming(true);
              }}
            >
              Disconnect repository
            </button>
          </div>
        )
      ) : null}
      {error ? <Alert tone="bad">{error}</Alert> : null}
    </section>
  );
}

function SettingsCard({
  projectId,
  connection,
  onSaved,
}: {
  projectId: string;
  connection: GitConnection;
  onSaved: (next: GitConnection) => void;
}) {
  const [draft, setDraft] = useState(() => ({
    review_pushes: connection.review_pushes,
    review_pull_requests: connection.review_pull_requests,
    review_forks: connection.review_forks,
    publish_checks: connection.publish_checks,
    publish_pull_requests: connection.publish_pull_requests,
    check_fail_threshold: connection.check_fail_threshold,
    reconcile_days: connection.reconcile_days,
  }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const edit = connection.can_edit;
  const branch = connection.repository?.default_branch ?? "the default branch";
  const flags: { key: Flag; label: string; hint?: string }[] = [
    { key: "review_pushes", label: `Review every push to ${branch}` },
    { key: "review_pull_requests", label: "Review pull requests" },
    {
      key: "review_forks",
      label: "Also review pull requests from forks automatically",
      hint: "Anyone can open a pull request from a fork; off means someone starts those by hand.",
    },
    {
      key: "publish_checks",
      label: "Post a check and one summary comment on GitHub",
      hint: "New problems appear on the pull request's changed lines.",
    },
    {
      key: "publish_pull_requests",
      label: "Allow opening pull requests with checked fixes",
      hint: "A person still clicks Open pull request; refactorX never merges.",
    },
  ];

  function save() {
    if (connection.version === null || connection.version === undefined) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    const body: GitConnectionUpdate = { version: connection.version, ...draft };
    updateGitConnection(projectId, body).then(
      (next) => {
        setBusy(false);
        setSaved(true);
        onSaved(next);
      },
      (caught: unknown) => {
        setError(describeError(caught));
        setBusy(false);
      },
    );
  }

  return (
    <section className="card stack" aria-labelledby="git-settings" data-testid="github-settings">
      <div className="stack stack-xs">
        <h2 id="git-settings" className="card-title">
          <Icon name="settings" size={16} /> What refactorX does
        </h2>
        <p className="card-sub">
          {edit ? "Changes apply to the next review." : "A workspace admin can change these."}
        </p>
      </div>
      <div className="stack stack-sm">
        {flags.map((flag) => (
          <label key={flag.key} className="checkbox-row">
            <input
              type="checkbox"
              checked={draft[flag.key]}
              disabled={!edit}
              data-testid={`setting-${flag.key}`}
              onChange={(event) => {
                setDraft({ ...draft, [flag.key]: event.target.checked });
              }}
            />
            <span className="stack stack-xs">
              <span>{flag.label}</span>
              {flag.hint ? <span className="small muted">{flag.hint}</span> : null}
            </span>
          </label>
        ))}
      </div>
      <div className="form-grid">
        <label>
          Make the check fail for new problems that are at least
          <select
            value={draft.check_fail_threshold}
            disabled={!edit || !draft.publish_checks}
            onChange={(event) => {
              setDraft({
                ...draft,
                check_fail_threshold: event.target.value as GitConnection["check_fail_threshold"],
              });
            }}
          >
            <option value="never">Never fail (information only)</option>
            <option value="critical">Critical</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
          </select>
        </label>
        <label>
          Re-check everything (no reuse of earlier results) every
          <span className="row">
            <input
              className="input-narrow"
              type="number"
              min={1}
              max={90}
              value={draft.reconcile_days}
              disabled={!edit}
              onChange={(event) => {
                setDraft({ ...draft, reconcile_days: Number(event.target.value) || 1 });
              }}
            />
            <span className="small">days</span>
          </span>
        </label>
      </div>
      {edit ? (
        <div className="row">
          <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={save}>
            {busy ? "Saving…" : "Save settings"}
          </button>
          {saved ? <span className="small secondary">Saved.</span> : null}
        </div>
      ) : null}
      {error ? <Alert tone="bad">{error}</Alert> : null}
    </section>
  );
}

function ReviewList({ reviews }: { reviews: CodeReview[] }) {
  if (reviews.length === 0) {
    return (
      <section className="card">
        <Empty title="No reviews yet">
          <p className="small secondary">Pushes and pull requests appear here once reviewed.</p>
        </Empty>
      </section>
    );
  }
  return (
    <section className="card" aria-labelledby="git-reviews">
      <h2 id="git-reviews" className="card-title">
        Reviews of this repository
      </h2>
      <div className="table-wrap">
        <table className="data-table" data-testid="code-reviews">
          <caption className="visually-hidden">Reviews of this repository</caption>
          <thead>
            <tr>
              <th scope="col">What</th>
              <th scope="col">Status</th>
              <th scope="col">Result</th>
              <th scope="col">Started</th>
            </tr>
          </thead>
          <tbody>
            {reviews.map((review) => (
              <tr key={review.id} data-testid="code-review-row">
                <th scope="row">
                  <a href={`#/reviews/${review.id}`}>
                    <ReviewTarget review={review} />
                  </a>
                </th>
                <td>
                  <StatusBadge state={review.state} />
                </td>
                <td>
                  <span className="stack stack-xs">
                    <ReviewOutcome review={review} />
                    {review.publish_state && PUBLISHED[review.publish_state] ? (
                      <span className="small muted">{PUBLISHED[review.publish_state]}</span>
                    ) : null}
                  </span>
                </td>
                <td className="nowrap">{formatRelative(review.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** Project tab: the connected GitHub repository, what is reviewed or posted, and its reviews. */
export function GitHubView({ projectId, workspaceId }: { projectId: string; workspaceId: string }) {
  const status = useAsync((signal) => fetchGitHubStatus(signal), []);
  const [refresh, setRefresh] = useState(0);
  const connection = useAsync(
    (signal) => fetchGitConnection(projectId, signal),
    [projectId, refresh],
  );
  const [tick, setTick] = useState(0);
  const reviews = useAsync(
    (signal) => listCodeReviews(projectId, signal),
    [projectId, refresh, tick],
  );
  const active = (reviews.data ?? []).some((r) => REVIEW_ACTIVE.has(r.state));
  const connected = connection.data?.connected === true;
  useEffect(() => {
    // Pushes and pull requests arrive at any time: refresh often while something runs.
    if (!connected) return;
    const timer = window.setTimeout(
      () => {
        setTick((n) => n + 1);
      },
      active ? 2000 : 10000,
    );
    return () => {
      window.clearTimeout(timer);
    };
  }, [active, connected, tick]);

  if (status.error) return <Alert tone="bad">{status.error}</Alert>;
  if (connection.error) return <Alert tone="bad">{connection.error}</Alert>;
  if (!status.data || !connection.data) return <Loading />;
  if (!status.data.available && !connection.data.connected) {
    return (
      <section className="card" data-testid="github-not-set-up">
        <Empty title="GitHub is not set up on this server">
          <p className="secondary">Uploads and folder captures work without it.</p>
          {status.data.admin_hint ? (
            <Disclosure summary="How to set it up">
              <p className="small secondary">{status.data.admin_hint}</p>
            </Disclosure>
          ) : null}
        </Empty>
      </section>
    );
  }
  const reload = () => {
    setRefresh((n) => n + 1);
  };
  if (!connection.data.connected) {
    return connection.data.can_edit ? (
      <ConnectCard projectId={projectId} workspaceId={workspaceId} onDone={reload} />
    ) : (
      <section className="card">
        <Empty title="No GitHub repository connected">
          <p className="small secondary">A workspace admin can connect one.</p>
        </Empty>
      </section>
    );
  }
  return (
    <div className="stack">
      <div className="split">
        <RepositoryCard projectId={projectId} connection={connection.data} onChange={reload} />
        <SettingsCard projectId={projectId} connection={connection.data} onSaved={reload} />
      </div>
      {reviews.error ? <Alert tone="bad">{reviews.error}</Alert> : null}
      {reviews.data ? <ReviewList reviews={reviews.data} /> : <Loading />}
    </div>
  );
}
