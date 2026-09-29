import { useMemo, useState, type KeyboardEvent } from "react";

import type { GraphEdge, GraphNode, GraphSummary } from "../api/client";
import {
  fetchGraphSummary,
  fetchImpact,
  fetchNeighborhood,
  searchGraphNodes,
} from "../api/endpoints";
import { Alert, Disclosure, Empty, Loading } from "../components/Common";
import { Icon } from "../components/Icon";
import { StatusBadge } from "../components/Status";
import { formatNumber, titleCase } from "../lib/format";
import { useAsync } from "../lib/useAsync";

const CLASSES = ["resolved", "declared", "inferred", "unresolved"] as const;
const CLASS_HELP: Record<string, string> = {
  resolved: "Confirmed: the target was found in your code",
  declared:
    "Declared: comes from a library or platform your project declares (for example the JDK)",
  inferred: "Likely: matched by name, not fully confirmed",
  unresolved:
    "Not found: could not be traced (missing library, undeclared package or dynamic import)",
};
const ECOSYSTEMS: Record<string, string> = { maven: "Maven", npm: "npm", gradle: "Gradle" };

/** "module:maven:app" → "app (Maven)"; other keys unchanged. */
function moduleName(key: string): string {
  const match = /^module:([^:]+):(.+)$/.exec(key);
  if (!match?.[1] || !match[2]) return key;
  return `${match[2]} (${ECOSYSTEMS[match[1]] ?? match[1]})`;
}
const GLYPH: Record<string, string> = {
  module: "M",
  file: "F",
  type: "T",
  package: "P",
  external: "E",
  function: "ƒ",
};

function short(text: string, max = 22): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function activate(handler: () => void) {
  return (event: KeyboardEvent) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      handler();
    }
  };
}

function Legend({ counts }: { counts: Record<string, number> }) {
  return (
    <div className="legend" aria-label="What the connection styles mean">
      {CLASSES.map((name) => (
        <span key={name} title={CLASS_HELP[name]}>
          <span className="legend-swatch" data-class={name} aria-hidden="true" />
          <StatusBadge state={name} /> {formatNumber(counts[name] ?? 0)}
        </span>
      ))}
    </div>
  );
}

function ModuleMap({
  summary,
  onSelect,
}: {
  summary: GraphSummary;
  onSelect: (node: GraphNode) => void;
}) {
  const modules = summary.modules;
  if (modules.length === 0) return <Empty title="No modules detected" />;
  if (modules.length > 24) {
    return (
      <p className="hint">{modules.length} modules: use the table below (map limited to 24).</p>
    );
  }
  const width = 760;
  const height = modules.length <= 2 ? 220 : 380;
  const cx = width / 2;
  const cy = height / 2;
  // Ellipse: wide boxes need more horizontal than vertical spacing.
  const rx = modules.length === 1 ? 0 : cx - 100;
  const ry = modules.length === 1 ? 0 : cy - 50;
  const position = new Map(
    modules.map((module, index) => {
      const angle = (2 * Math.PI * index) / modules.length - Math.PI / 2;
      return [
        module.node.key,
        { x: cx + rx * Math.cos(angle), y: cy + ry * Math.sin(angle) },
      ] as const;
    }),
  );
  return (
    <svg
      className="graph-canvas"
      viewBox={`0 0 ${String(width)} ${String(height)}`}
      role="img"
      aria-label={`Module map: ${String(modules.length)} modules, ${String(summary.module_dependencies.length)} dependencies. The table below lists the same data.`}
    >
      <defs>
        <marker
          id="arrow-m"
          viewBox="0 0 10 10"
          refX="10"
          refY="5"
          markerWidth="7"
          markerHeight="7"
          orient="auto-start-reverse"
        >
          <path d="M0,0 L10,5 L0,10 z" fill="var(--text-muted)" />
        </marker>
      </defs>
      {summary.module_dependencies.map((dep) => {
        const from = position.get(dep.source_key);
        const to = position.get(dep.target_key);
        if (!from || !to) return null;
        const dx = to.x - from.x;
        const dy = to.y - from.y;
        const length = Math.hypot(dx, dy) || 1;
        const pad = 80;
        const offset = dep.relation === "depends_on" ? 6 : -6;
        const nx = (-dy / length) * offset;
        const ny = (dx / length) * offset;
        return (
          <g key={`${dep.source_key}-${dep.target_key}-${dep.relation}`}>
            <line
              className="graph-edge"
              data-class={dep.classification}
              x1={from.x + (dx / length) * pad + nx}
              y1={from.y + (dy / length) * pad + ny}
              x2={to.x - (dx / length) * pad + nx}
              y2={to.y - (dy / length) * pad + ny}
              markerEnd="url(#arrow-m)"
            >
              <title>
                {`${dep.source_key} → ${dep.target_key}: ${String(dep.edges)} ${dep.relation === "depends_on" ? "manifest dependency" : "code reference(s)"} (${dep.classification})`}
              </title>
            </line>
          </g>
        );
      })}
      {modules.map((module) => {
        const point = position.get(module.node.key);
        if (!point) return null;
        return (
          <g
            key={module.node.key}
            className="graph-node"
            data-kind="module"
            transform={`translate(${String(point.x)},${String(point.y)})`}
            tabIndex={0}
            role="button"
            aria-label={`Module ${module.node.label}: ${String(module.files)} files`}
            onClick={() => {
              onSelect(module.node);
            }}
            onKeyDown={activate(() => {
              onSelect(module.node);
            })}
            style={{ cursor: "pointer" }}
          >
            <rect x={-78} y={-26} width={156} height={52} rx={12} />
            <text textAnchor="middle" y={-4}>
              {short(module.node.label, 20)}
            </text>
            <text className="graph-glyph" textAnchor="middle" y={14}>
              {`${String(module.files)} files · ${String(module.types)} types`}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function radialLayout(center: GraphNode, nodes: GraphNode[], edges: GraphEdge[]) {
  const adjacency = new Map<number, Set<number>>();
  for (const edge of edges) {
    if (edge.target_id === null) continue;
    for (const [a, b] of [
      [edge.source_id, edge.target_id],
      [edge.target_id, edge.source_id],
    ] as const) {
      if (!adjacency.has(a)) adjacency.set(a, new Set());
      adjacency.get(a)?.add(b);
    }
  }
  const depth = new Map<number, number>([[center.id, 0]]);
  const queue = [center.id];
  while (queue.length) {
    const current = queue.shift() as number;
    for (const next of adjacency.get(current) ?? []) {
      if (!depth.has(next)) {
        depth.set(next, (depth.get(current) ?? 0) + 1);
        queue.push(next);
      }
    }
  }
  const rings = new Map<number, GraphNode[]>();
  for (const node of nodes) {
    const d = depth.get(node.id) ?? 2;
    if (!rings.has(d)) rings.set(d, []);
    rings.get(d)?.push(node);
  }
  const positions = new Map<number, { x: number; y: number }>();
  const cx = 480;
  const cy = 260;
  for (const [d, members] of rings) {
    const radius = d === 0 ? 0 : d === 1 ? 150 : 235;
    members.forEach((node, index) => {
      const angle = (2 * Math.PI * index) / Math.max(members.length, 1) - Math.PI / 2 + d * 0.3;
      positions.set(node.id, {
        x: cx + radius * Math.cos(angle),
        y: cy + radius * Math.sin(angle),
      });
    });
  }
  return positions;
}

function Neighborhood({
  snapshotId,
  node,
  onSelect,
}: {
  snapshotId: string;
  node: GraphNode;
  onSelect: (node: GraphNode) => void;
}) {
  const [depth, setDepth] = useState(1);
  const [impactOn, setImpactOn] = useState(false);
  const hood = useAsync(
    (signal) => fetchNeighborhood(snapshotId, node.id, depth, signal),
    [snapshotId, node.id, depth],
  );
  const impact = useAsync(
    (signal) => (impactOn ? fetchImpact(snapshotId, node.id, 3, signal) : Promise.resolve(null)),
    [snapshotId, node.id, impactOn],
  );
  const data = hood.data;
  const positions = useMemo(
    () =>
      data
        ? radialLayout(data.center, data.nodes, data.edges)
        : new Map<number, { x: number; y: number }>(),
    [data],
  );
  const byId = new Map((data?.nodes ?? []).map((n) => [n.id, n]));
  return (
    <section className="card stack" aria-labelledby="hood-title" data-testid="neighborhood">
      <div className="card-head" style={{ marginBottom: 0 }}>
        <div>
          <h2 id="hood-title" className="card-title">
            <Icon name="graph" size={16} /> {node.label}
          </h2>
          <p className="card-sub mono">
            {titleCase(node.kind)} · {node.path ?? node.key}
            {node.start_line ? `:${String(node.start_line)}` : ""}
          </p>
        </div>
        <div className="row" role="group" aria-label="Neighborhood depth">
          {[1, 2].map((value) => (
            <button
              key={value}
              type="button"
              className="pill-toggle"
              aria-pressed={depth === value}
              onClick={() => {
                setDepth(value);
              }}
            >
              {value} hop{value === 1 ? "" : "s"}
            </button>
          ))}
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            aria-pressed={impactOn}
            onClick={() => {
              setImpactOn((value) => !value);
            }}
          >
            <Icon name="pulse" size={14} /> Impact
          </button>
        </div>
      </div>
      {hood.error ? <Alert tone="bad">{hood.error}</Alert> : null}
      {!data ? <Loading /> : null}
      {data ? (
        <>
          {data.truncated ? (
            <Alert tone="info">
              Showing a bounded neighborhood ({data.nodes.length} nodes); some relations were
              omitted. Search for a node to explore further.
            </Alert>
          ) : null}
          <svg
            className="graph-canvas"
            viewBox="0 0 960 520"
            role="img"
            aria-label={`Neighborhood of ${node.label}: ${String(data.nodes.length)} nodes and ${String(data.edges.length)} relations. The table below lists every relation.`}
          >
            <defs>
              <marker
                id="arrow-n"
                viewBox="0 0 10 10"
                refX="10"
                refY="5"
                markerWidth="6"
                markerHeight="6"
                orient="auto-start-reverse"
              >
                <path d="M0,0 L10,5 L0,10 z" fill="var(--text-muted)" />
              </marker>
            </defs>
            {data.edges.map((edge) => {
              const from = positions.get(edge.source_id);
              const to = edge.target_id !== null ? positions.get(edge.target_id) : undefined;
              if (!from || !to) return null;
              const dx = to.x - from.x;
              const dy = to.y - from.y;
              const length = Math.hypot(dx, dy) || 1;
              return (
                <line
                  key={edge.id}
                  className="graph-edge"
                  data-class={edge.classification}
                  x1={from.x + (dx / length) * 18}
                  y1={from.y + (dy / length) * 18}
                  x2={to.x - (dx / length) * 21}
                  y2={to.y - (dy / length) * 21}
                  markerEnd="url(#arrow-n)"
                >
                  <title>{`${edge.relation} ${edge.target_ref} (${edge.classification})`}</title>
                </line>
              );
            })}
            {data.nodes.map((item) => {
              const point = positions.get(item.id);
              if (!point) return null;
              const center = item.id === data.center.id;
              return (
                <g
                  key={item.id}
                  className="graph-node"
                  data-kind={item.kind}
                  data-center={center}
                  transform={`translate(${String(point.x)},${String(point.y)})`}
                  tabIndex={0}
                  role="button"
                  aria-label={`${titleCase(item.kind)} ${item.label}${center ? " (selected)" : ""}`}
                  style={{ cursor: "pointer" }}
                  onClick={() => {
                    onSelect(item);
                  }}
                  onKeyDown={activate(() => {
                    onSelect(item);
                  })}
                >
                  <circle r={center ? 20 : 16} />
                  <text className="graph-glyph" textAnchor="middle" y={4}>
                    {GLYPH[item.kind] ?? "?"}
                  </text>
                  <text textAnchor="middle" y={center ? 38 : 33}>
                    {short(item.label, 26)}
                  </text>
                </g>
              );
            })}
          </svg>
          <div className="legend small">
            <span>M module · F file · T class or type · P package · E external library</span>
            <span>Dashed or dotted lines: connections outside your code or not confirmed</span>
          </div>
          <div className="table-wrap" style={{ maxHeight: 360 }}>
            <table className="data-table">
              <caption className="visually-hidden">Connections around this item</caption>
              <thead>
                <tr>
                  <th scope="col">From</th>
                  <th scope="col">Connection</th>
                  <th scope="col">To</th>
                  <th scope="col">Confidence</th>
                  <th scope="col">Where in the code</th>
                </tr>
              </thead>
              <tbody>
                {data.edges.map((edge) => (
                  <tr key={edge.id} data-testid="graph-edge-row">
                    <td className="small">{byId.get(edge.source_id)?.label ?? edge.source_id}</td>
                    <td className="small">{edge.relation.replaceAll("_", " ")}</td>
                    <th scope="row" className="mono small" style={{ fontWeight: 500 }}>
                      {edge.target_ref}
                    </th>
                    <td className="small">
                      <StatusBadge state={edge.classification} />
                      <div className="muted">{edge.reason}</div>
                    </td>
                    <td className="mono small">
                      {edge.evidence_path
                        ? `${edge.evidence_path}${edge.evidence_start_line ? `:${String(edge.evidence_start_line)}` : ""}`
                        : "—"}
                      {edge.evidence_text ? (
                        <div className="muted">{short(edge.evidence_text, 60)}</div>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
      {impactOn ? (
        <div className="stack" data-testid="impact">
          <h3 className="card-title" style={{ fontSize: "0.95rem" }}>
            What depends on this (up to 3 steps away)
          </h3>
          {impact.error ? <Alert tone="bad">{impact.error}</Alert> : null}
          {impact.data ? (
            <>
              {impact.data.dependents.length === 0 ? (
                <p className="small secondary">Nothing in the code depends on this.</p>
              ) : (
                <ul className="stack" style={{ listStyle: "none", padding: 0, margin: 0 }}>
                  {impact.data.dependents.map((item) => (
                    <li key={item.node.id} className="row">
                      <span className="badge badge-neutral">hop {item.depth}</span>
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm mono"
                        onClick={() => {
                          onSelect(item.node);
                        }}
                      >
                        {item.node.path ?? item.node.label}
                      </button>
                      <span className="small muted">via {item.via.replaceAll("_", " ")}</span>
                    </li>
                  ))}
                </ul>
              )}
              <Alert tone="info">
                <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
                  {impact.data.caveats.map((caveat) => (
                    <li key={caveat}>{caveat}</li>
                  ))}
                </ul>
              </Alert>
            </>
          ) : (
            <Loading lines={2} />
          )}
        </div>
      ) : null}
    </section>
  );
}

function NodeSearch({
  snapshotId,
  onSelect,
}: {
  snapshotId: string;
  onSelect: (node: GraphNode) => void;
}) {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const results = useAsync(
    (signal) => searchGraphNodes(snapshotId, { q: query, kind }, signal),
    [snapshotId, query, kind],
  );
  return (
    <section className="card stack" aria-labelledby="search-title">
      <h2 id="search-title" className="card-title">
        Find a file, class or package
      </h2>
      <div className="row">
        <input
          type="search"
          aria-label="Search graph nodes"
          placeholder="Type a name…"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
          }}
        />
        <select
          aria-label="Node kind"
          value={kind}
          style={{ width: "auto" }}
          onChange={(event) => {
            setKind(event.target.value);
          }}
        >
          <option value="">Everything</option>
          {["module", "file", "type", "package", "external"].map((value) => (
            <option key={value} value={value}>
              {titleCase(value)}
            </option>
          ))}
        </select>
      </div>
      {results.error ? <Alert tone="bad">{results.error}</Alert> : null}
      <ul
        className="stack"
        style={{ listStyle: "none", padding: 0, margin: 0, maxHeight: 360, overflow: "auto" }}
      >
        {(results.data?.items ?? []).map((item) => (
          <li key={item.id}>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              style={{ width: "100%", justifyContent: "flex-start" }}
              data-testid="graph-node-result"
              onClick={() => {
                onSelect(item);
              }}
            >
              <span className="badge badge-neutral">{GLYPH[item.kind] ?? "?"}</span>
              <span className="mono small" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
                {item.path ?? item.label}
              </span>
            </button>
          </li>
        ))}
      </ul>
      {results.data ? (
        <p className="hint">
          {results.data.total} match{results.data.total === 1 ? "" : "es"}
          {results.data.next_cursor ? " · type more of the name to narrow the list" : ""}
        </p>
      ) : null}
    </section>
  );
}

export function ArchitectureView({ snapshotId }: { snapshotId: string }) {
  const summary = useAsync((signal) => fetchGraphSummary(snapshotId, signal), [snapshotId]);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  if (summary.error) return <Alert tone="bad">{summary.error}</Alert>;
  if (!summary.data) return <Loading />;
  const data = summary.data;
  if (data.status === "none") {
    return (
      <section className="card">
        <Empty title="No architecture map yet">
          <p className="small">Review this upload to map its modules, files and connections.</p>
        </Empty>
      </section>
    );
  }
  if (data.status === "failed") return <Alert tone="bad">{data.message}</Alert>;
  const build = data.build;
  const gaps = Object.entries(data.unresolved_reasons);
  return (
    <div className="stack" data-testid="architecture">
      <section className="card stack" aria-labelledby="graph-title">
        <div className="card-head" style={{ marginBottom: 0 }}>
          <div>
            <h2 id="graph-title" className="card-title">
              <Icon name="graph" size={16} /> Architecture map
            </h2>
            <p className="card-sub">
              How your code is organised and how its parts depend on each other. Built from the code
              itself; nothing is guessed by AI.
            </p>
          </div>
          {build && build.state !== "SUCCEEDED" ? <StatusBadge state={build.state} /> : null}
        </div>
        {build ? (
          <div className="tiles tiles-3">
            {[
              ["Files mapped", build.files_parsed],
              ["Parts", build.node_count],
              ["Connections", build.edge_count],
            ].map(([label, value]) => (
              <div key={String(label)} className="tile">
                <span className="tile-value">{formatNumber(Number(value))}</span>
                <span className="tile-label">{label}</span>
              </div>
            ))}
          </div>
        ) : null}
        <Legend counts={data.edges_by_classification} />
      </section>
      <section className="card stack" aria-labelledby="modules-title">
        <h2 id="modules-title" className="card-title">
          Modules
        </h2>
        <ModuleMap summary={data} onSelect={setSelected} />
        <Disclosure summary="Show module connections as a table" testId="module-table">
          <div className="table-wrap" style={{ maxHeight: 300 }}>
            <table className="data-table">
              <caption className="visually-hidden">Module connections</caption>
              <thead>
                <tr>
                  <th scope="col">From</th>
                  <th scope="col">To</th>
                  <th scope="col">Declared in</th>
                  <th scope="col">Connections</th>
                  <th scope="col">Confidence</th>
                </tr>
              </thead>
              <tbody>
                {data.module_dependencies.map((dep) => (
                  <tr
                    key={`${dep.source_key}-${dep.target_key}-${dep.relation}`}
                    data-testid="module-dependency"
                  >
                    <th scope="row" className="small" style={{ fontWeight: 500 }}>
                      {moduleName(dep.source_key)}
                    </th>
                    <td className="small">{moduleName(dep.target_key)}</td>
                    <td className="small">
                      {dep.relation === "depends_on" ? "build file" : "code"}
                    </td>
                    <td>{dep.edges}</td>
                    <td>
                      {dep.classification === "mixed" ? (
                        <StatusBadge state="PARTIAL" label="Mixed" />
                      ) : (
                        <StatusBadge state={dep.classification} />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Disclosure>
      </section>
      <NodeSearch snapshotId={snapshotId} onSelect={setSelected} />
      {selected ? (
        <Neighborhood snapshotId={snapshotId} node={selected} onSelect={setSelected} />
      ) : (
        <section className="card">
          <Empty title="Pick a module above or search for a name">
            <p className="small">
              See what it connects to and what would be affected if it changes.
            </p>
          </Empty>
        </section>
      )}
      <Disclosure testId="architecture-technical">
        {build ? (
          <dl className="kv">
            <dt>Connections not traced</dt>
            <dd>{formatNumber(build.unresolved_count)}</dd>
            <dt>Files that could not be read</dt>
            <dd>{formatNumber(build.files_failed)}</dd>
            <dt>Map builder</dt>
            <dd className="mono">{build.extractor}</dd>
          </dl>
        ) : null}
        {gaps.length > 0 ? (
          <>
            <strong className="small">Why some connections could not be traced</strong>
            <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
              {gaps.map(([reason, count]) => (
                <li key={reason}>
                  <strong>{count}</strong> {reason}
                </li>
              ))}
            </ul>
          </>
        ) : null}
        <p className="hint">
          {data.message}. Untraced connections are listed, never guessed: missing libraries,
          undeclared packages and dynamic imports stay untraced.
        </p>
      </Disclosure>
    </div>
  );
}
