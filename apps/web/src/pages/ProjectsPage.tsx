import { useId, useState, type SubmitEvent } from "react";

import { describeError, type Principal } from "../api/client";
import { createProject, listProjects } from "../api/endpoints";
import { Alert, Empty, Loading } from "../components/Common";
import { Icon } from "../components/Icon";
import { formatDate } from "../lib/format";
import { navigate } from "../lib/router";
import { useAsync } from "../lib/useAsync";

function CreateProjectForm({ principal }: { principal: Principal }) {
  const nameId = useId();
  const descriptionId = useId();
  const workspaceId = useId();
  const writable = principal.workspaces.filter((w) => w.role !== "viewer");
  const [workspace, setWorkspace] = useState(writable[0]?.workspace_id ?? "");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (writable.length === 0) {
    return <p className="muted">You have no workspace where you can create projects.</p>;
  }

  async function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const project = await createProject({
        workspaceId: workspace,
        name: name.trim(),
        description,
      });
      navigate(`#/projects/${project.id}?tab=source`);
    } catch (caught) {
      setError(describeError(caught));
      setBusy(false);
    }
  }

  return (
    <form
      className="card stack"
      aria-labelledby="create-project-title"
      onSubmit={(event) => {
        void submit(event);
      }}
    >
      <h2 id="create-project-title" className="card-title">
        New project
      </h2>
      {writable.length > 1 ? (
        <div className="field">
          <label htmlFor={workspaceId}>Workspace</label>
          <select
            id={workspaceId}
            value={workspace}
            onChange={(event) => {
              setWorkspace(event.target.value);
            }}
          >
            {writable.map((w) => (
              <option key={w.workspace_id} value={w.workspace_id}>
                {w.name}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      <div className="field">
        <label htmlFor={nameId}>Name</label>
        <input
          id={nameId}
          required
          maxLength={200}
          value={name}
          placeholder="e.g. Billing service"
          onChange={(event) => {
            setName(event.target.value);
          }}
        />
      </div>
      <div className="field">
        <label htmlFor={descriptionId}>Description (optional)</label>
        <textarea
          id={descriptionId}
          maxLength={2000}
          rows={2}
          value={description}
          onChange={(event) => {
            setDescription(event.target.value);
          }}
        />
      </div>
      {error ? <Alert tone="bad">{error}</Alert> : null}
      <div>
        <button type="submit" className="btn btn-primary" disabled={busy || name.trim() === ""}>
          <Icon name="projects" size={16} />
          {busy ? "Creating…" : "Create project"}
        </button>
      </div>
    </form>
  );
}

export function ProjectsPage({ principal }: { principal: Principal }) {
  const projects = useAsync((signal) => listProjects(signal), []);
  const names = new Map(principal.workspaces.map((w) => [w.workspace_id, w.name]));
  return (
    <div className="split">
      <section className="card" aria-labelledby="projects-title">
        <div className="card-head">
          <h2 id="projects-title" className="card-title">
            All projects
          </h2>
          <span className="muted small">{projects.data?.length ?? 0} total</span>
        </div>
        <div aria-live="polite">
          {projects.error ? <Alert tone="bad">{projects.error}</Alert> : null}
          {projects.loading && !projects.data ? <Loading /> : null}
          {projects.data?.length === 0 ? (
            <Empty title="No projects yet">
              <p>Create one to upload source and run a scan.</p>
            </Empty>
          ) : null}
          {projects.data && projects.data.length > 0 ? (
            <div className="table-wrap">
              <table className="data-table">
                <caption className="visually-hidden">Projects you can access</caption>
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Workspace</th>
                    <th scope="col">Origin</th>
                    <th scope="col">Created</th>
                  </tr>
                </thead>
                <tbody>
                  {projects.data.map((project) => (
                    <tr key={project.id}>
                      <th scope="row">
                        <a href={`#/projects/${project.id}`}>{project.name}</a>
                        <div className="muted small mono">{project.slug}</div>
                      </th>
                      <td>{names.get(project.workspace_id) ?? project.workspace_id}</td>
                      <td>
                        {project.origin === "synthetic_fixture" ? (
                          <span className="badge badge-neutral">Synthetic fixture</span>
                        ) : (
                          "User"
                        )}
                      </td>
                      <td>{formatDate(project.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      </section>
      <CreateProjectForm principal={principal} />
    </div>
  );
}
