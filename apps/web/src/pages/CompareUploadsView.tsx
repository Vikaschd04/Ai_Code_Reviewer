/** Compare two uploads (or commits) of one project: what was added, changed, removed and renamed,
 * and each file side by side or inline. Loaded lazily (it carries the code viewer). */
import { useState } from "react";

import { type SnapshotComparison } from "../api/client";
import { compareSnapshotFile, compareSnapshots, listSnapshots } from "../api/endpoints";
import { CompareView } from "../components/CodeEditor";
import { Alert, Empty, Loading } from "../components/Common";
import { FileLocation } from "../components/FileLocation";
import { Icon } from "../components/Icon";
import { formatDate } from "../lib/format";
import { plural } from "../lib/labels";
import { useAsync } from "../lib/useAsync";

const STATUS: Record<string, { label: string; tone: string }> = {
  added: { label: "Added", tone: "ok" },
  modified: { label: "Changed", tone: "neutral" },
  removed: { label: "Removed", tone: "bad" },
  renamed: { label: "Moved", tone: "neutral" },
};

type Change = SnapshotComparison["changes"][number];

function FileDiff({
  snapshotId,
  baseId,
  change,
}: {
  snapshotId: string;
  baseId: string;
  change: Change;
}) {
  const [layout, setLayout] = useState<"side" | "inline">(() =>
    window.matchMedia("(min-width: 1100px)").matches ? "side" : "inline",
  );
  const file = useAsync(
    (signal) =>
      compareSnapshotFile(snapshotId, baseId, change.path, change.previous_path ?? null, signal),
    [snapshotId, baseId, change.path, change.previous_path],
  );
  if (file.error) return <Alert tone="bad">{file.error}</Alert>;
  if (!file.data) return <Loading lines={6} />;
  return (
    <section className="card stack" aria-labelledby="cmp-file-title" data-testid="compare-file">
      <div className="card-head">
        <h2 id="cmp-file-title" className="card-title">
          <FileLocation path={change.path} />
        </h2>
        <div className="btn-group" role="group" aria-label="Comparison layout">
          {(["side", "inline"] as const).map((value) => (
            <button
              key={value}
              type="button"
              className="btn btn-ghost btn-sm"
              aria-pressed={layout === value}
              onClick={() => {
                setLayout(value);
              }}
            >
              {value === "side" ? "Side by side" : "Inline"}
            </button>
          ))}
        </div>
      </div>
      {change.previous_path ? (
        <p className="small muted">Moved from {change.previous_path}</p>
      ) : null}
      {file.data.note ? <p className="small muted">{file.data.note}</p> : null}
      <CompareView
        path={change.path}
        before={file.data.before ?? ""}
        after={file.data.after ?? ""}
        layout={layout}
      />
    </section>
  );
}

export function CompareUploadsView({
  snapshotId,
  projectId,
}: {
  snapshotId: string;
  projectId: string;
}) {
  const uploads = useAsync((signal) => listSnapshots(projectId, signal), [projectId]);
  const others = (uploads.data ?? []).filter(
    (item) => item.id !== snapshotId && item.capture_status === "FROZEN",
  );
  const [baseId, setBaseId] = useState<string | null>(null);
  const chosen = baseId ?? others[0]?.id ?? null;
  const [selected, setSelected] = useState<Change | null>(null);
  const comparison = useAsync(
    (signal) => (chosen ? compareSnapshots(snapshotId, chosen, signal) : Promise.resolve(null)),
    [snapshotId, chosen],
  );
  if (uploads.error) return <Alert tone="bad">{uploads.error}</Alert>;
  if (!uploads.data) return <Loading />;
  if (others.length === 0) {
    return (
      <section className="card">
        <Empty title="Nothing to compare yet">
          <p className="small secondary">Upload another version of this project to compare.</p>
        </Empty>
      </section>
    );
  }
  const data = comparison.data;
  const current = selected ?? data?.changes[0] ?? null;
  return (
    <div className="stack">
      <section className="card stack" aria-labelledby="cmp-title" data-testid="compare-uploads">
        <div className="card-head">
          <div>
            <h2 id="cmp-title" className="card-title">
              <Icon name="branch" size={16} /> Compare with another upload
            </h2>
            <p className="card-sub">
              Shows the stored text files that differ. Binary and excluded files are not compared.
            </p>
          </div>
          <select
            aria-label="Older upload"
            value={chosen ?? ""}
            onChange={(event) => {
              setBaseId(event.target.value);
              setSelected(null);
            }}
          >
            {others.map((item) => (
              <option key={item.id} value={item.id}>
                {item.source_name} · {formatDate(item.frozen_at ?? item.created_at)}
              </option>
            ))}
          </select>
        </div>
        {comparison.error ? <Alert tone="bad">{comparison.error}</Alert> : null}
        {!data ? (
          comparison.error ? null : (
            <Loading />
          )
        ) : data.changes.length === 0 ? (
          <p className="small">These uploads have the same text files.</p>
        ) : (
          <>
            <p className="small secondary" data-testid="compare-counts">
              {plural(data.counts.added ?? 0, "file")} added ·{" "}
              {plural(data.counts.modified ?? 0, "file")} changed ·{" "}
              {plural(data.counts.removed ?? 0, "file")} removed ·{" "}
              {plural(data.counts.renamed ?? 0, "file")} moved
              {data.truncated ? " (list shortened)" : ""}
            </p>
            <ul className="result-list" data-testid="compare-changes">
              {data.changes.map((change) => (
                <li key={`${change.status}:${change.path}`}>
                  <button
                    type="button"
                    className="result-item"
                    aria-current={current?.path === change.path ? "true" : undefined}
                    onClick={() => {
                      setSelected(change);
                    }}
                  >
                    <span className={`badge badge-${STATUS[change.status]?.tone ?? "neutral"}`}>
                      {STATUS[change.status]?.label ?? change.status}
                    </span>
                    <span className="mono">{change.path}</span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
      {chosen && current ? (
        <FileDiff
          key={`${chosen}:${current.path}`}
          snapshotId={snapshotId}
          baseId={chosen}
          change={current}
        />
      ) : null}
    </div>
  );
}
