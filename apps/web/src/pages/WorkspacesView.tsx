import { useState } from "react";

import { describeError } from "../api/client";
import { createWorkspace, listWorkspaces } from "../api/endpoints";
import { Alert, Empty, Loading } from "../components/Common";
import { Icon } from "../components/Icon";
import { StatusBadge } from "../components/Status";
import { formatRelative } from "../lib/format";
import { plural } from "../lib/labels";
import { navigate, workspaceHref } from "../lib/router";
import { useAsync } from "../lib/useAsync";

/** Project tab: fix workspaces (many fixes on top of one upload), newest first. */
export function WorkspacesView({
  projectId,
  hasUpload,
}: {
  projectId: string;
  hasUpload: boolean;
}) {
  const workspaces = useAsync((signal) => listWorkspaces(projectId, signal), [projectId]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const create = (
    <button
      type="button"
      className="btn btn-primary"
      disabled={busy || !hasUpload}
      onClick={() => {
        setBusy(true);
        setError(null);
        createWorkspace(projectId).then(
          (created) => {
            navigate(workspaceHref(created.id));
          },
          (caught: unknown) => {
            setError(describeError(caught));
            setBusy(false);
          },
        );
      }}
      data-testid="workspace-new"
    >
      <Icon name="wrench" size={15} /> {busy ? "Opening…" : "New workspace"}
    </button>
  );
  return (
    <section className="card stack" aria-labelledby="workspaces-title">
      <div className="card-head">
        <div>
          <h2 id="workspaces-title" className="card-title">
            <Icon name="wrench" size={16} /> Fix workspaces
          </h2>
          <p className="card-sub">
            Fix many issues at once, by hand or automatically, check the result and download a patch
            or only the changed files. Your upload is never changed.
          </p>
        </div>
        {create}
      </div>
      {!hasUpload ? <Alert tone="info">Upload code first.</Alert> : null}
      {error ? <Alert tone="bad">{error}</Alert> : null}
      {workspaces.error ? <Alert tone="bad">{workspaces.error}</Alert> : null}
      {!workspaces.data ? (
        workspaces.error ? null : (
          <Loading />
        )
      ) : workspaces.data.length === 0 ? (
        <Empty title="No workspaces yet">
          <p className="small secondary">
            A workspace starts from the latest upload. Open one to begin fixing.
          </p>
        </Empty>
      ) : (
        <div className="table-wrap">
          <table className="data-table" data-testid="workspaces">
            <caption className="visually-hidden">Fix workspaces</caption>
            <thead>
              <tr>
                <th scope="col">Workspace</th>
                <th scope="col">Changes</th>
                <th scope="col">Last check</th>
                <th scope="col">Updated</th>
              </tr>
            </thead>
            <tbody>
              {workspaces.data.map((item) => (
                <tr key={item.id}>
                  <th scope="row">
                    <a href={workspaceHref(item.id)}>{item.title}</a>
                    <div className="cell-sub small muted">On {item.base_name}</div>
                  </th>
                  <td className="nowrap">{plural(item.files_changed, "file")}</td>
                  <td>
                    <StatusBadge state={item.latest_check_state ?? "unchecked"} />
                  </td>
                  <td className="nowrap">{formatRelative(item.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
