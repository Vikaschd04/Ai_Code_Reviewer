import { useEffect, useRef, useState } from "react";

import { describeError, type GitHubLinkResult, type GitInstallation } from "../api/client";
import {
  completeGitHubLink,
  fetchGitHubStatus,
  listGitInstallations,
  startGitHubLink,
  syncGitInstallation,
  unlinkGitInstallation,
} from "../api/endpoints";
import { Alert, Disclosure, Empty, Loading, PageHeader } from "../components/Common";
import { Icon } from "../components/Icon";
import { plural } from "../lib/labels";
import { formatRelative } from "../lib/format";
import { useSession } from "../lib/session";
import { useAsync } from "../lib/useAsync";

function adminWorkspace(roles: { workspace_id: string; role: string }[] | undefined) {
  return roles?.find((w) => w.role === "owner" || w.role === "admin")?.workspace_id ?? null;
}

function Installation({
  workspaceId,
  installation,
  onChange,
}: {
  workspaceId: string;
  installation: GitInstallation;
  onChange: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    action().then(
      () => {
        setBusy(false);
        setConfirming(false);
        onChange();
      },
      (caught: unknown) => {
        setError(describeError(caught));
        setBusy(false);
      },
    );
  }

  return (
    <section className="card stack" data-testid="git-installation">
      <div className="card-head">
        <div className="stack stack-xs">
          <h2 className="card-title">
            <Icon name="users" size={16} /> {installation.account}
          </h2>
          <p className="card-sub">
            {plural(installation.repositories.length, "repository", "repositories")}
            {installation.synced_at
              ? ` · list updated ${formatRelative(installation.synced_at)}`
              : ""}
          </p>
        </div>
        <div className="row">
          {installation.revoked ? (
            <span className="badge badge-bad">
              <Icon name="x" size={13} /> Uninstalled on GitHub
            </span>
          ) : installation.suspended ? (
            <span className="badge badge-warn">
              <Icon name="alert" size={13} /> Suspended on GitHub
            </span>
          ) : null}
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            disabled={busy || installation.revoked}
            onClick={() => {
              run(() => syncGitInstallation(workspaceId, installation.id));
            }}
          >
            Refresh list
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            disabled={busy}
            onClick={() => {
              setConfirming(true);
            }}
          >
            Unlink
          </button>
        </div>
      </div>
      {confirming ? (
        <Alert tone="warn">
          <span className="stack stack-sm">
            <span>
              Unlinking stops reviews for every connected repository of {installation.account}.
              Earlier reviews and results stay.
            </span>
            <span className="row">
              <button
                type="button"
                className="btn btn-danger btn-sm"
                disabled={busy}
                onClick={() => {
                  run(() => unlinkGitInstallation(workspaceId, installation.id));
                }}
              >
                Unlink {installation.account}
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
      ) : null}
      {installation.repositories.length === 0 ? (
        <p className="small muted">No repositories are shared with refactorX yet.</p>
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <caption className="visually-hidden">Repositories of {installation.account}</caption>
            <thead>
              <tr>
                <th scope="col">Repository</th>
                <th scope="col">Default branch</th>
                <th scope="col">Project</th>
              </tr>
            </thead>
            <tbody>
              {installation.repositories.map((repo) => (
                <tr key={repo.id} data-testid="git-repository">
                  <th scope="row">
                    <span className="row">
                      <span className="mono small">{repo.full_name}</span>
                      {repo.private ? <span className="chip">Private</span> : null}
                      {repo.removed ? <span className="chip">No longer shared</span> : null}
                    </span>
                  </th>
                  <td className="mono small">{repo.default_branch}</td>
                  <td>
                    {repo.project_id ? (
                      <a href={`#/projects/${repo.project_id}?tab=github`}>{repo.project_name}</a>
                    ) : (
                      <span className="small muted">Not connected</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {error ? <Alert tone="bad">{error}</Alert> : null}
    </section>
  );
}

/** Workspace admins: connect GitHub accounts through the refactorX app and see their repositories. */
export function GitHubPage({ params }: { params: Record<string, string> }) {
  const { principal } = useSession();
  const workspaceId = principal?.is_demo ? null : adminWorkspace(principal?.workspaces);
  const status = useAsync((signal) => fetchGitHubStatus(signal), []);
  const [refresh, setRefresh] = useState(0);
  const installations = useAsync(
    (signal) => (workspaceId ? listGitInstallations(workspaceId, signal) : Promise.resolve([])),
    [workspaceId, refresh],
  );
  const [result, setResult] = useState<GitHubLinkResult | null>(null);
  const [error, setError] = useState<string | null>(
    params.error ? "GitHub did not confirm access." : null,
  );
  const [busy, setBusy] = useState(false);
  const completed = useRef(false);

  useEffect(() => {
    const { code, state } = params;
    if (!code || !state || !workspaceId || completed.current) return;
    completed.current = true;
    window.history.replaceState(null, "", "#/github"); // keep the one-time code out of history
    setBusy(true);
    completeGitHubLink(workspaceId, code, state).then(
      (linked) => {
        setResult(linked);
        setBusy(false);
        setRefresh((n) => n + 1);
      },
      (caught: unknown) => {
        setError(describeError(caught));
        setBusy(false);
      },
    );
  }, [params, workspaceId]);

  const header = (
    <PageHeader
      title="GitHub"
      sub="Connect repositories so pushes and pull requests are reviewed automatically."
    />
  );
  if (status.error)
    return (
      <>
        {header}
        <Alert tone="bad">{status.error}</Alert>
      </>
    );
  if (!status.data)
    return (
      <>
        {header}
        <Loading />
      </>
    );
  if (!status.data.available) {
    return (
      <>
        {header}
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
      </>
    );
  }
  if (!workspaceId) {
    return (
      <>
        {header}
        <Alert tone="info">Only workspace admins can connect GitHub accounts.</Alert>
      </>
    );
  }
  const installUrl = status.data.install_url;
  return (
    <>
      {header}
      <section className="card stack" aria-labelledby="connect-title" data-testid="github-connect">
        <div className="stack stack-xs">
          <h2 id="connect-title" className="card-title">
            <Icon name="branch" size={16} /> Connect a GitHub account
          </h2>
          <p className="card-sub">
            refactorX reads code. It posts checks, comments or pull requests only in projects where
            an admin switches that on, and it never merges anything.
          </p>
        </div>
        <ol className="numbered-steps">
          <li className="stack stack-xs">
            <strong>Install the refactorX app on GitHub</strong>
            <span className="small secondary">
              Choose the account or organization and the repositories refactorX may read.
            </span>
            {installUrl ? (
              <span className="row">
                <a
                  className="btn btn-ghost btn-sm"
                  href={installUrl}
                  target="_blank"
                  rel="noreferrer"
                >
                  <Icon name="external" size={14} /> Install on GitHub
                </a>
                {params.installed ? (
                  <span className="small">Installed. Now confirm below.</span>
                ) : null}
              </span>
            ) : null}
          </li>
          <li className="stack stack-xs">
            <strong>Confirm it is yours</strong>
            <span className="small secondary">
              GitHub asks you to approve, then brings you back here. Only accounts you can manage on
              GitHub are linked.
            </span>
            <span className="row">
              <button
                type="button"
                className="btn btn-primary btn-sm"
                disabled={busy || !status.data.linking_available}
                data-testid="github-confirm"
                onClick={() => {
                  setBusy(true);
                  setError(null);
                  startGitHubLink(workspaceId).then(
                    (url) => {
                      window.location.assign(url);
                    },
                    (caught: unknown) => {
                      setError(describeError(caught));
                      setBusy(false);
                    },
                  );
                }}
              >
                {busy ? "Working…" : "Confirm access"}
              </button>
              {!status.data.linking_available ? (
                <span className="small muted">Linking is not fully set up on this server.</span>
              ) : null}
            </span>
          </li>
        </ol>
        {result ? (
          <Alert tone="info">
            <span data-testid="github-link-result">
              {result.linked.length > 0
                ? `Linked ${result.linked.map((i) => i.account).join(", ")}.`
                : "Nothing new was linked."}
              {result.skipped.map((s) => ` ${s.account}: ${s.reason}.`).join("")}
            </span>
          </Alert>
        ) : null}
        {error ? <Alert tone="bad">{error}</Alert> : null}
      </section>
      {installations.error ? <Alert tone="bad">{installations.error}</Alert> : null}
      {installations.data && installations.data.length > 0 ? (
        <div className="stack">
          {installations.data.map((installation) => (
            <Installation
              key={installation.id}
              workspaceId={workspaceId}
              installation={installation}
              onChange={() => {
                setRefresh((n) => n + 1);
              }}
            />
          ))}
        </div>
      ) : null}
    </>
  );
}
