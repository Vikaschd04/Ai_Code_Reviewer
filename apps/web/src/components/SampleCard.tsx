import { useState } from "react";

import { describeError } from "../api/client";
import { navigate } from "../lib/router";
import { runSampleReview, SAMPLE_STEP_LABELS, type SampleStep } from "../lib/sample";
import { Alert } from "./Common";
import { Icon } from "./Icon";

/** One-click dry run on the built-in sample project (a small, deliberately flawed web shop). */
export function SampleCard({
  workspaceId,
  compact = false,
}: {
  workspaceId: string;
  compact?: boolean;
}) {
  const [step, setStep] = useState<SampleStep | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setError(null);
    try {
      const scan = await runSampleReview(workspaceId, setStep);
      navigate(`#/scans/${scan.id}`);
    } catch (caught) {
      setError(describeError(caught));
      setStep(null);
    }
  }

  return (
    <section
      className={compact ? "card stack sample-card compact" : "card stack sample-card"}
      aria-labelledby="sample-title"
      data-testid="sample-card"
    >
      <div className="media">
        <span className="feature-icon" aria-hidden="true">
          <Icon name="box" size={22} />
        </span>
        <div className="stack stack-sm">
          <h2 id="sample-title" className="card-title">
            Try the sample project
          </h2>
          <p className="secondary small">
            A small Java and TypeScript online store with deliberate problems — security flaws,
            bugs, vulnerable libraries and a leaked (fake) password. One click uploads it and runs a
            full review so you can see every result screen.
          </p>
        </div>
      </div>
      <div className="row">
        <button
          type="button"
          className="btn btn-primary"
          disabled={step !== null}
          onClick={() => {
            void run();
          }}
        >
          <Icon name="play" size={15} />
          {step ? "Working…" : "Run the sample review"}
        </button>
        <span className="small muted" role="status" aria-live="polite">
          {step ? SAMPLE_STEP_LABELS[step] : "Takes about a minute on a small server."}
        </span>
      </div>
      {error ? <Alert tone="bad">{error}</Alert> : null}
    </section>
  );
}
