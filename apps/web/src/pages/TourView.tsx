import type { Tour } from "../api/client";
import { fetchTour } from "../api/endpoints";
import { Alert, Empty, Loading } from "../components/Common";
import { Icon } from "../components/Icon";
import { plural } from "../lib/labels";
import { useAsync } from "../lib/useAsync";

// A code tour for newcomers (ADD-A7): a one-page brief of the newest reviewed upload, built from
// the review only (inventory, dependency map, symbols, open issues). Every item names its files.

function Files({
  items,
  testId,
  empty,
}: {
  items: Tour["start_here"];
  testId: string;
  empty: string;
}) {
  if (items.length === 0) return <p className="small muted">{empty}</p>;
  return (
    <ol className="stack stack-xs" data-testid={testId}>
      {items.map((item) => (
        <li key={item.path} className="small">
          <span className="mono wrap-anywhere">{item.path}</span>{" "}
          <span className="muted">— {item.reason}</span>
        </li>
      ))}
    </ol>
  );
}

export function TourView({ projectId }: { projectId: string }) {
  const tour = useAsync((signal) => fetchTour(projectId, signal), [projectId]);
  if (tour.error) return <Alert tone="bad">{tour.error}</Alert>;
  if (!tour.data) return <Loading />;
  const data = tour.data;
  if (!data.snapshot_id) {
    return (
      <Empty title="No review yet">
        <p className="small">Upload code and review it to get a tour of it.</p>
      </Empty>
    );
  }
  return (
    <section className="card stack" aria-labelledby="tour-title" data-testid="code-tour">
      <div className="stack stack-xs">
        <h2 id="tour-title" className="card-title">
          <Icon name="layers" size={16} /> Code tour
        </h2>
        <p className="card-sub">
          A one-page brief for people new to this code, from the latest review. Every item names the
          files it comes from; nothing here is written by AI.
        </p>
      </div>

      <div className="stack stack-xs" data-testid="tour-made-of">
        <h3 className="subheading">What it is made of</h3>
        <p className="small">
          {data.languages
            .map(
              (l) =>
                `${l.language} (${plural(l.files, "file")}, ${l.lines.toLocaleString()} lines)`,
            )
            .join(" · ") || "No source files were recognised."}
        </p>
        {data.technologies.length > 0 ? (
          <p className="small secondary">Declared: {data.technologies.join(", ")}</p>
        ) : null}
      </div>

      <div className="stack stack-xs" data-testid="tour-parts">
        <h3 className="subheading">Its parts</h3>
        {!data.map_available ? (
          <p className="small muted">No dependency map for this upload.</p>
        ) : data.parts.length === 0 ? (
          <p className="small muted">No parts with code were found.</p>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Part</th>
                  <th scope="col">Files</th>
                  <th scope="col">Lines</th>
                  <th scope="col">Uses</th>
                  <th scope="col">Used by</th>
                </tr>
              </thead>
              <tbody>
                {data.parts.map((part) => (
                  <tr key={part.name}>
                    <td className="mono nowrap">{part.name}</td>
                    <td>{part.files}</td>
                    <td>{part.lines.toLocaleString()}</td>
                    <td className="mono nowrap">{part.uses.join(", ") || "—"}</td>
                    <td>{part.used_by > 0 ? plural(part.used_by, "part") : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="stack stack-xs">
        <h3 className="subheading">Start reading here</h3>
        <Files
          items={data.start_here}
          testId="tour-start-here"
          empty="No file is used by others in the dependency map."
        />
      </div>
      <div className="stack stack-xs">
        <h3 className="subheading">Entry points</h3>
        <Files
          items={data.entry_points}
          testId="tour-entry-points"
          empty="No main method or function was found; the code may start from a framework."
        />
      </div>
      <div className="stack stack-xs">
        <h3 className="subheading">Handle with care</h3>
        <Files items={data.hotspots} testId="tour-hotspots" empty="No open issues." />
      </div>
      {data.docs.length > 0 ? (
        <div className="stack stack-xs">
          <h3 className="subheading">Documents in the upload</h3>
          <p className="small mono wrap-anywhere">{data.docs.join(", ")}</p>
        </div>
      ) : null}
    </section>
  );
}
