/** Architecture rules (P10 slice 2): the team's intended layers and forbidden dependencies as
 * versioned YAML, checked on every review. Plain summary first; editing, the check on the
 * latest upload and the version history are one click away. */
import { useState } from "react";

import {
  ApiError,
  describeError,
  type ArchitectureRules as Rules,
  type ArchitectureRulesCheck,
} from "../api/client";
import {
  architectureRulesExportUrl,
  checkArchitectureRules,
  fetchArchitectureRules,
  fetchArchitectureRulesYaml,
  saveArchitectureRules,
} from "../api/endpoints";
import { formatNumber, formatRelative } from "../lib/format";
import { plural } from "../lib/labels";
import { useAsync } from "../lib/useAsync";
import { CodeEditor } from "./CodeEditor";
import { Alert, Disclosure, Loading } from "./Common";
import { Icon } from "./Icon";

const TEMPLATE = `# Layers, top to bottom. Each part of your code (a Java package or a folder) belongs to the
# first layer that matches it: * matches within one name part, ** any number of parts.
layers:
  - name: web
    match: [com.example.web.**, src/ui/**]
  - name: service
    match: [com.example.service.**]
  - name: domain
    match: [com.example.domain.**]
# lower: a layer may use any layer below it; next: only the one directly below; none.
layering: lower
# Dependencies that are never allowed (a layer name or a pattern on each side).
forbid:
  - from: domain
    to: com.example.legacy.**
    reason: The legacy package is being removed.
# Exceptions to layering, with a reason and an optional expiry date.
allow: []
`;

function problemsOf(error: unknown): string[] {
  if (error instanceof ApiError && Array.isArray(error.details.problems)) {
    return error.details.problems.filter((p): p is string => typeof p === "string");
  }
  return [];
}

function CheckResult({ data, saved }: { data: ArchitectureRulesCheck; saved: boolean }) {
  return (
    <div className="stack stack-sm" data-testid="rules-check">
      <p className="small">
        <strong>
          {data.violation_count === 0
            ? "No breaches"
            : plural(data.violation_count, "breach", "breaches")}
        </strong>{" "}
        {saved ? "in the latest upload" : "would be reported on the latest upload"}
        {data.allowed_by_exception > 0
          ? ` · ${plural(data.allowed_by_exception, "use")} allowed by an exception`
          : ""}
        {data.unassigned_count > 0 ? ` · ${plural(data.unassigned_count, "part")} in no layer` : ""}
        .
      </p>
      {data.notes.map((note) => (
        <Alert key={note} tone="warn">
          {note}
        </Alert>
      ))}
      {data.expired_exceptions.map((item) => (
        <p key={item} className="small muted">
          Exception no longer applies: {item}
        </p>
      ))}
      {data.violations.length > 0 ? (
        <ul className="stack stack-xs plain-list" data-testid="rules-check-breaches">
          {data.violations.slice(0, 20).map((v) => (
            <li key={`${v.rule_id}:${v.path}:${v.target}`} className="stack stack-xs">
              <span className="small">
                <strong>{v.title}</strong>
              </span>
              <span className="mono small secondary wrap-anywhere">
                {v.path}
                {v.line ? `:${String(v.line)}` : ""} → {v.target}
              </span>
            </li>
          ))}
          {data.violation_count > 20 ? (
            <li className="small muted">… and {formatNumber(data.violation_count - 20)} more</li>
          ) : null}
        </ul>
      ) : null}
      <Disclosure summary="Which parts are in which layer" testId="rules-check-layers">
        <ul className="stack stack-sm plain-list">
          {data.layers.map((layer) => (
            <li key={layer.name} className="stack stack-xs">
              <span className="small">
                <strong>{layer.name}</strong> · {plural(layer.part_count, "part")}
              </span>
              <span className="mono small secondary wrap-anywhere">
                {layer.parts.join(", ") || "No part matches this layer yet."}
                {layer.part_count > layer.parts.length ? " …" : ""}
              </span>
            </li>
          ))}
          {data.unassigned_count > 0 ? (
            <li className="stack stack-xs">
              <span className="small">
                <strong>In no layer</strong> (checked only by forbid rules)
              </span>
              <span className="mono small secondary wrap-anywhere">
                {data.unassigned.join(", ")}
                {data.unassigned_count > data.unassigned.length ? " …" : ""}
              </span>
            </li>
          ) : null}
        </ul>
        <p className="hint">
          Checked {plural(data.dependencies_checked, "dependency", "dependencies")} between parts of
          the upload&apos;s dependency map. Test code is left out.
        </p>
      </Disclosure>
    </div>
  );
}

export function ArchitectureRules({
  projectId,
  snapshotId,
}: {
  projectId: string;
  snapshotId: string | null;
}) {
  const [stamp, setStamp] = useState(0);
  const rules = useAsync((signal) => fetchArchitectureRules(projectId, signal), [projectId, stamp]);
  const latest = useAsync(
    () =>
      snapshotId && rules.data?.version
        ? checkArchitectureRules(snapshotId, null).catch(() => null)
        : Promise.resolve(null),
    [snapshotId, rules.data?.version],
  );
  const [draft, setDraft] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"check" | "save" | "load" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [preview, setPreview] = useState<ArchitectureRulesCheck | null>(null);

  if (rules.error) return <Alert tone="bad">{rules.error}</Alert>;
  if (!rules.data) return <Loading />;
  const data: Rules = rules.data;
  const document = data.document;
  const layers = document?.layers ?? [];
  const forbid = document?.forbid ?? [];
  const allow = document?.allow ?? [];

  const fail = (caught: unknown) => {
    setProblems(problemsOf(caught));
    setError(describeError(caught));
  };
  const startEditing = () => {
    setError(null);
    setProblems([]);
    setPreview(null);
    if (!data.version) {
      setDraft(TEMPLATE);
      return;
    }
    setBusy("load");
    fetchArchitectureRulesYaml(projectId).then(
      (text) => {
        setDraft(text);
        setBusy(null);
      },
      (caught: unknown) => {
        fail(caught);
        setBusy(null);
      },
    );
  };
  const check = () => {
    if (draft === null || !snapshotId) return;
    setBusy("check");
    setError(null);
    setProblems([]);
    checkArchitectureRules(snapshotId, draft).then(
      (result) => {
        setPreview(result);
        setBusy(null);
      },
      (caught: unknown) => {
        setPreview(null);
        fail(caught);
        setBusy(null);
      },
    );
  };
  const save = () => {
    if (draft === null) return;
    setBusy("save");
    setError(null);
    setProblems([]);
    saveArchitectureRules(projectId, draft, data.current_version, note).then(
      () => {
        setDraft(null);
        setNote("");
        setPreview(null);
        setBusy(null);
        setStamp((value) => value + 1);
      },
      (caught: unknown) => {
        fail(caught);
        setBusy(null);
      },
    );
  };

  return (
    <section className="card stack" aria-labelledby="rules-title" data-testid="architecture-rules">
      <div>
        <h2 id="rules-title" className="card-title">
          <Icon name="layers" size={16} /> Architecture rules
        </h2>
        <p className="card-sub">
          How your code should be organised: layers from top to bottom and dependencies that are not
          allowed. Every review checks them and reports breaches as issues.
        </p>
      </div>
      {document && draft === null ? (
        <div className="stack stack-sm" data-testid="rules-summary">
          {layers.length > 0 ? (
            <ol className="chip-list" aria-label="Layers, top to bottom">
              {layers.map((layer, index) => (
                <li key={layer.name} className="chip">
                  {index > 0 ? <span aria-hidden="true">↓</span> : null}
                  {layer.name}
                </li>
              ))}
            </ol>
          ) : null}
          <p className="small secondary">
            {document.layering === "none" || layers.length < 2
              ? "No layer order"
              : document.layering === "next"
                ? "Each layer may use only the layer directly below it"
                : "Each layer may use the layers below it"}
            {" · "}
            {plural(forbid.length, "forbidden dependency", "forbidden dependencies")}
            {" · "}
            {plural(allow.length, "exception")}
          </p>
          <p className="small muted">
            Version {data.version}
            {data.created_by ? ` · saved by ${data.created_by}` : ""}
            {data.created_at ? ` · ${formatRelative(data.created_at)}` : ""}
            {data.note ? ` · “${data.note}”` : ""}
          </p>
          {latest.data ? <CheckResult data={latest.data} saved /> : null}
        </div>
      ) : null}
      {!document && draft === null ? (
        <p className="small" data-testid="rules-empty">
          No rules yet. Start from an example and adapt the names to your code.
        </p>
      ) : null}
      {draft === null ? (
        <div className="row">
          {data.can_edit ? (
            <button
              type="button"
              className="btn btn-primary"
              onClick={startEditing}
              disabled={busy === "load"}
            >
              <Icon name="code" size={15} /> {document ? "Edit rules" : "Write rules"}
            </button>
          ) : null}
          {document ? (
            <>
              <a
                className="btn btn-ghost"
                href={`#/projects/${projectId}?tab=issues&check=architecture`}
              >
                Breaches in Issues <Icon name="arrow" size={15} />
              </a>
              <a className="btn btn-ghost" href={architectureRulesExportUrl(projectId)} download>
                <Icon name="download" size={15} /> Download YAML
              </a>
            </>
          ) : null}
        </div>
      ) : (
        <div className="stack stack-sm" data-testid="rules-editor">
          <CodeEditor path="architecture-rules.yaml" value={draft} onChange={setDraft} />
          <div className="field">
            <label htmlFor="rules-note">What changed (optional)</label>
            <input
              id="rules-note"
              type="text"
              maxLength={500}
              value={note}
              onChange={(event) => {
                setNote(event.target.value);
              }}
            />
          </div>
          <div className="row">
            <button
              type="button"
              className="btn btn-primary"
              onClick={save}
              disabled={busy !== null}
            >
              <Icon name="check" size={15} /> {busy === "save" ? "Saving…" : "Save rules"}
            </button>
            {snapshotId ? (
              <button
                type="button"
                className="btn btn-ghost"
                onClick={check}
                disabled={busy !== null}
              >
                {busy === "check" ? "Checking…" : "Check on the latest upload"}
              </button>
            ) : null}
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => {
                setDraft(null);
                setPreview(null);
                setError(null);
                setProblems([]);
              }}
            >
              Cancel
            </button>
          </div>
          <p className="hint">
            Saving adds a new version; the next review applies it. Nothing in your code changes.
          </p>
          {preview ? <CheckResult data={preview} saved={false} /> : null}
        </div>
      )}
      {error ? (
        <Alert tone="bad">
          {error}
          {problems.length > 0 ? (
            <ul className="plain-list small" data-testid="rules-problems">
              {problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          ) : null}
        </Alert>
      ) : null}
      {data.history.length > 0 ? (
        <Disclosure summary={`History (${plural(data.history.length, "version")})`}>
          <div className="table-wrap table-scroll">
            <table className="data-table" data-testid="rules-history">
              <caption className="visually-hidden">Saved versions of the rules</caption>
              <thead>
                <tr>
                  <th scope="col" className="num">
                    Version
                  </th>
                  <th scope="col">Saved</th>
                  <th scope="col">Note</th>
                  <th scope="col">Rules</th>
                  <th scope="col">
                    <span className="visually-hidden">Download</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.history.map((item) => (
                  <tr key={item.version}>
                    <td className="num">{item.version}</td>
                    <td className="small">
                      {item.created_by ?? "Unknown"} · {formatRelative(item.created_at)}
                      {item.source === "yaml" ? " · imported" : ""}
                    </td>
                    <td className="small">{item.note ?? "—"}</td>
                    <td className="small secondary">
                      {plural(item.layers, "layer")}, {item.forbid} forbidden,{" "}
                      {plural(item.allow, "exception")}
                    </td>
                    <td>
                      <a
                        className="small"
                        href={architectureRulesExportUrl(projectId, item.version)}
                        download
                      >
                        YAML
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Disclosure>
      ) : null}
    </section>
  );
}
