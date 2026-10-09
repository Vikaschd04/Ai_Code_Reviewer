/** Structure health (P10): parts of the code, how they depend on each other, cycles with the
 * cheapest imports to cut, and Martin's metrics in collapsed technical details. */
import { type ArchitectureMetrics } from "../api/client";
import { fetchArchitecture } from "../api/endpoints";
import { formatNumber } from "../lib/format";
import { plural } from "../lib/labels";
import { useAsync } from "../lib/useAsync";
import { Alert, Disclosure, Loading } from "./Common";
import { Icon } from "./Icon";
import { StatusBadge } from "./Status";

type Component = ArchitectureMetrics["components"][number];

const ZONE: Record<string, { label: string; tone: string; hint: string }> = {
  pain: {
    label: "Hard to change",
    tone: "warn",
    hint: "Concrete and depended on by many parts: every change ripples outwards.",
  },
  uselessness: {
    label: "Unused abstraction",
    tone: "neutral",
    hint: "Mostly interfaces or abstract types that little depends on.",
  },
};

function value(number: number | null | undefined): string {
  return number === null || number === undefined ? "—" : number.toFixed(2);
}

function attention(component: Component): string[] {
  const notes: string[] = [];
  if (component.in_cycle) notes.push("In a cycle");
  const zone = component.zone ? ZONE[component.zone] : undefined;
  if (zone) notes.push(zone.label);
  return notes;
}

function Matrix({ data }: { data: ArchitectureMetrics }) {
  const parts = [...data.components]
    .sort((a, b) => b.fan_in + b.fan_out - (a.fan_in + a.fan_out) || a.key.localeCompare(b.key))
    .slice(0, 12)
    .map((c) => c.key);
  const weight = new Map(data.edges.map((e) => [`${e.source}→${e.target}`, e.weight]));
  const cyclic = new Set(
    data.cycles.flatMap((cycle) => cycle.edges.map((e) => `${e.source}→${e.target}`)),
  );
  return (
    <div className="table-wrap table-scroll">
      <table className="data-table dsm" data-testid="architecture-dsm">
        <caption className="visually-hidden">
          Dependency matrix: each row uses the parts in the columns
        </caption>
        <thead>
          <tr>
            <th scope="col">Uses →</th>
            {parts.map((part, index) => (
              <th key={part} scope="col" title={part}>
                {index + 1}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {parts.map((row, rowIndex) => (
            <tr key={row}>
              <th scope="row" className="mono small">
                {rowIndex + 1}. {row}
              </th>
              {parts.map((column) => {
                const key = `${row}→${column}`;
                const count = weight.get(key);
                return (
                  <td
                    key={column}
                    className={`num${cyclic.has(key) ? " dsm-cycle" : ""}`}
                    title={count ? `${row} uses ${column}: ${String(count)}` : undefined}
                  >
                    {row === column ? "·" : count ? String(count) : ""}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ArchitectureHealth({ snapshotId }: { snapshotId: string }) {
  const metrics = useAsync((signal) => fetchArchitecture(snapshotId, signal), [snapshotId]);
  if (metrics.error) return <Alert tone="bad">{metrics.error}</Alert>;
  if (!metrics.data) return <Loading />;
  const data = metrics.data;
  const summary = data.summary;
  const flagged = data.components.filter((c) => attention(c).length > 0).slice(0, 12);
  const tiles = [
    { label: "Parts", value: summary.components, state: null },
    { label: "Cycles", value: summary.cycles, state: summary.cycles > 0 ? "PARTIAL" : "ok" },
    {
      label: "Hard to change",
      value: summary.zone_of_pain,
      state: summary.zone_of_pain > 0 ? "PARTIAL" : "ok",
    },
    { label: "Unused abstractions", value: summary.zone_of_uselessness, state: null },
  ];
  return (
    <section
      className="card stack"
      aria-labelledby="health-title"
      data-testid="architecture-health"
    >
      <div>
        <h2 id="health-title" className="card-title">
          <Icon name="shield" size={16} /> Structure health
        </h2>
        <p className="card-sub">
          How tangled and how flexible the parts of your code are (Java packages and folders).
          Measured from the code; nothing is guessed.
        </p>
      </div>
      <div className="tiles tiles-4">
        {tiles.map((tile) => (
          <div key={tile.label} className="tile" data-tile={tile.label}>
            <span className="tile-value">{formatNumber(tile.value)}</span>
            <span className="tile-label">
              {tile.state ? <StatusBadge state={tile.state} label={tile.label} /> : tile.label}
            </span>
          </div>
        ))}
      </div>
      {data.cycles.length > 0 ? (
        <div className="stack stack-sm" data-testid="architecture-cycles">
          <h3 className="subheading">Cycles</h3>
          <p className="small secondary">
            Parts that depend on each other in a circle can only change together. Removing the
            imports below breaks each cycle with the fewest changes.
          </p>
          <ul className="stack stack-sm plain-list">
            {data.cycles.map((cycle, index) => (
              <li key={index} className="stack stack-xs">
                <span className="mono small">
                  {cycle.components.join(" ↔ ")}
                  {cycle.component_count > cycle.components.length
                    ? ` … (${plural(cycle.component_count, "part")})`
                    : ""}
                </span>
                <span className="small">
                  Cut:{" "}
                  {cycle.cut
                    .slice(0, 8)
                    .map((e) => `${e.source} → ${e.target} (${plural(e.weight, "import")})`)
                    .join("; ")}
                  {cycle.cut_count > 8
                    ? ` … ${plural(cycle.cut_count, "dependency")} in all (${plural(cycle.cut_weight, "import")})`
                    : ""}
                  {cycle.exact ? "" : " (approximate: a large tangle)"}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="small" data-testid="architecture-no-cycles">
          <Icon name="check" size={14} /> No cycles between parts.
        </p>
      )}
      {flagged.length > 0 ? (
        <div className="stack stack-sm">
          <h3 className="subheading">Needs attention</h3>
          <ul className="stack stack-sm plain-list" data-testid="architecture-attention">
            {flagged.map((component) => (
              <li key={component.key} className="row">
                <span className="mono small">{component.key}</span>
                {attention(component).map((note) => (
                  <span key={note} className="badge badge-warn">
                    {note}
                  </span>
                ))}
                <span className="small muted">
                  {component.zone ? ZONE[component.zone]?.hint : ""}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {data.notes.map((note) => (
        <p key={note} className="small muted">
          {note}
        </p>
      ))}
      <Disclosure summary="All parts and their measurements" testId="architecture-metrics">
        <div className="table-wrap table-scroll">
          <table className="data-table">
            <caption className="visually-hidden">Parts and their measurements</caption>
            <thead>
              <tr>
                <th scope="col">Part</th>
                <th scope="col" className="num">
                  Files
                </th>
                <th scope="col" className="num">
                  Used by (Ca)
                </th>
                <th scope="col" className="num">
                  Uses (Ce)
                </th>
                <th scope="col" className="num">
                  Instability
                </th>
                <th scope="col" className="num">
                  Abstractness
                </th>
                <th scope="col" className="num">
                  Distance
                </th>
              </tr>
            </thead>
            <tbody>
              {data.components.map((c) => (
                <tr key={c.key} data-component={c.key}>
                  <th scope="row" className="mono small">
                    {c.key}
                  </th>
                  <td className="num">{c.files}</td>
                  <td className="num">{c.afferent}</td>
                  <td className="num">{c.efferent}</td>
                  <td className="num">{value(c.instability)}</td>
                  <td className="num">{value(c.abstractness)}</td>
                  <td className="num">{value(c.distance)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="hint">
          Used by (Ca): files elsewhere that use this part. Uses (Ce): files here that use other
          parts. Instability = Ce / (Ca + Ce): 0 = everything depends on it, 1 = it depends on
          everything. Abstractness: share of interfaces and abstract types. Distance |A + I − 1|:
          far from the balance line means hard to change (concrete and stable) or unused abstraction
          (abstract and unstable). — means not measurable (no dependencies or no types). After R. C.
          Martin.
        </p>
      </Disclosure>
      <Disclosure summary="Dependency matrix" testId="architecture-matrix">
        <Matrix data={data} />
        <p className="hint">
          Each row uses the parts in the columns (numbers are imports). Highlighted cells are part
          of a cycle. Shows the 12 most connected parts.
        </p>
      </Disclosure>
    </section>
  );
}
