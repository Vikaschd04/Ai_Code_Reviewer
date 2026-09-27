import { useEffect, useRef, useState } from "react";

import { describeError, type DiagnosticRun } from "../api/client";
import { fetchDiagnostic, fetchReadiness, startDiagnostic } from "../api/endpoints";
import { Alert, Loading } from "../components/Common";
import { Icon } from "../components/Icon";
import { ReadinessTable } from "../components/ReadinessTable";
import { StatusBadge } from "../components/Status";
import { useAsync } from "../lib/useAsync";

const POLL_INTERVAL_MS = 1000;
const POLL_LIMIT = 90;

function DiagnosticPanel({ onFinished }: { onFinished: () => void }) {
  const [run, setRun] = useState<DiagnosticRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const cancelled = useRef(false);

  useEffect(() => {
    cancelled.current = false;
    return () => {
      cancelled.current = true;
    };
  }, []);

  async function execute() {
    setBusy(true);
    setError(null);
    setRun(null);
    try {
      let current = await startDiagnostic();
      setRun(current);
      for (let attempt = 0; attempt < POLL_LIMIT && current.status === "RUNNING"; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
        if (cancelled.current) return;
        current = await fetchDiagnostic(current.workflow_id);
        setRun(current);
      }
      if (current.status === "RUNNING") {
        setError("The diagnostic is still running; check that a worker is polling.");
      }
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      if (!cancelled.current) {
        setBusy(false);
        onFinished();
      }
    }
  }

  const result = run?.result ?? null;
  return (
    <section className="card" aria-labelledby="diagnostic-title">
      <div className="card-head">
        <div>
          <h2 id="diagnostic-title" className="card-title">
            Workflow diagnostic
          </h2>
          <p className="card-sub">
            Runs a real Temporal workflow that writes, verifies and deletes a probe artifact and
            checks the schema. It proves the execution path only; it analyses no code.
          </p>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => {
            void execute();
          }}
          disabled={busy}
        >
          <Icon name="pulse" size={16} />
          {busy ? "Running diagnostic…" : "Run diagnostic workflow"}
        </button>
      </div>
      <div aria-live="polite">
        {error ? <Alert tone="bad">{error}</Alert> : null}
        {run ? (
          <dl className="kv" data-testid="diagnostic-result">
            <dt>Workflow</dt>
            <dd className="hash">{run.workflow_id}</dd>
            <dt>Status</dt>
            <dd data-testid="diagnostic-status">{run.status}</dd>
            {result ? (
              <>
                <dt>Worker</dt>
                <dd>{result.worker_identity}</dd>
                <dt>Artifact round trip</dt>
                <dd>
                  {result.artifact.read_back_verified ? "verified" : "MISMATCH"},{" "}
                  {result.artifact.size_bytes} bytes, sha256{" "}
                  <span className="hash">{result.artifact.sha256.slice(0, 16)}…</span>,{" "}
                  {result.artifact.deleted ? "cleaned up" : "NOT cleaned up"}
                </dd>
                <dt>Database schema</dt>
                <dd>
                  {result.database.schema_revision ?? "none"} (expected{" "}
                  {result.database.expected_revision})
                </dd>
              </>
            ) : null}
            {run.failure_message ? (
              <>
                <dt>Failure</dt>
                <dd>{run.failure_message}</dd>
              </>
            ) : null}
          </dl>
        ) : null}
      </div>
    </section>
  );
}

export function OperationsPage({ canOperate }: { canOperate: boolean }) {
  const readiness = useAsync((signal) => fetchReadiness(signal), []);
  const report = readiness.data;
  return (
    <>
      <section className="card" aria-labelledby="readiness-title">
        <div className="card-head">
          <div>
            <h2 id="readiness-title" className="card-title">
              Service readiness
            </h2>
            <p className="card-sub">Live checks of the real dependencies behind this deployment.</p>
          </div>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={readiness.reload}
            disabled={readiness.loading}
          >
            {readiness.loading ? "Checking…" : "Re-check"}
          </button>
        </div>
        <div aria-live="polite">
          {readiness.error ? <Alert tone="bad">{readiness.error}</Alert> : null}
          {report ? (
            <>
              <p className="row" data-testid="overall-readiness" style={{ marginTop: 0 }}>
                <StatusBadge state={report.status} /> Checked{" "}
                {new Date(report.checked_at).toLocaleTimeString()} ({report.environment}{" "}
                environment)
              </p>
              <div className="table-wrap">
                <ReadinessTable checks={report.checks} />
              </div>
            </>
          ) : readiness.loading ? (
            <Loading />
          ) : null}
        </div>
      </section>
      {canOperate ? <DiagnosticPanel onFinished={readiness.reload} /> : null}
    </>
  );
}
