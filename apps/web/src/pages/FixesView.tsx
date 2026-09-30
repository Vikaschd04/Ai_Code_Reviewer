import { listFixes } from "../api/endpoints";
import { Alert, Empty, Loading } from "../components/Common";
import { FixStatus } from "../components/Fixes";
import { formatRelative } from "../lib/format";
import { useAsync } from "../lib/useAsync";

/** Project tab: every fix prepared for this project, newest first. */
export function FixesView({ projectId }: { projectId: string }) {
  const fixes = useAsync((signal) => listFixes(projectId, signal), [projectId]);
  if (fixes.error) return <Alert tone="bad">{fixes.error}</Alert>;
  if (!fixes.data) return <Loading />;
  if (fixes.data.length === 0) {
    return (
      <section className="card">
        <Empty title="No fixes yet">
          <p className="small secondary">
            Open a finding and choose <strong>Prepare fix</strong> where an automatic fix exists.
          </p>
        </Empty>
      </section>
    );
  }
  return (
    <section className="card">
      <div className="table-wrap">
        <table className="data-table" data-testid="fixes">
          <caption className="visually-hidden">Fixes</caption>
          <thead>
            <tr>
              <th scope="col">Fix</th>
              <th scope="col">File</th>
              <th scope="col">Status</th>
              <th scope="col">Prepared</th>
            </tr>
          </thead>
          <tbody>
            {fixes.data.map((fix) => (
              <tr key={fix.id}>
                <th scope="row">
                  <a href={`#/fixes/${fix.id}`}>{fix.title}</a>
                  {fix.finding ? <div className="muted small">{fix.finding.title}</div> : null}
                </th>
                <td className="mono small truncate">{fix.path}</td>
                <td>
                  <FixStatus state={fix.state} />
                </td>
                <td className="nowrap">{formatRelative(fix.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
