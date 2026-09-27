import type { DependencyCheck } from "../api/client";
import { StatusBadge } from "./Status";

const NAMES: Record<string, string> = {
  database: "PostgreSQL database",
  workflow_service: "Temporal workflow service",
  workflow_worker: "Workflow worker",
  artifact_store: "Artifact store",
};

function formatDetail(value: string | number | boolean | null): string {
  return value === null ? "—" : String(value);
}

export function ReadinessTable({ checks }: { checks: DependencyCheck[] }) {
  return (
    <table className="data-table">
      <caption className="visually-hidden">Dependency readiness checks</caption>
      <thead>
        <tr>
          <th scope="col">Dependency</th>
          <th scope="col">Status</th>
          <th scope="col">Summary</th>
          <th scope="col">Details</th>
          <th scope="col">Latency</th>
        </tr>
      </thead>
      <tbody>
        {checks.map((check) => (
          <tr key={check.name} data-testid={`check-${check.name}`}>
            <th scope="row">{NAMES[check.name] ?? check.name}</th>
            <td>
              <StatusBadge state={check.status} />
            </td>
            <td>
              {check.summary}
              {check.error_code ? <code className="error-code">{check.error_code}</code> : null}
            </td>
            <td>
              {Object.keys(check.details ?? {}).length === 0 ? (
                "—"
              ) : (
                <dl className="details">
                  {Object.entries(check.details ?? {}).map(([key, value]) => (
                    <div key={key}>
                      <dt>{key.replaceAll("_", " ")}</dt>
                      <dd>{formatDetail(value)}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </td>
            <td>{check.latency_ms === null ? "—" : `${check.latency_ms} ms`}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
