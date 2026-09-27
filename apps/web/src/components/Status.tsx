import { Icon, type IconName } from "./Icon";

type Tone = "ok" | "warn" | "bad" | "neutral" | "live";

const STATES: Record<string, { tone: Tone; icon: IconName; label: string }> = {
  ok: { tone: "ok", icon: "check", label: "OK" },
  ready: { tone: "ok", icon: "check", label: "Ready" },
  READY: { tone: "ok", icon: "check", label: "Ready" },
  SUCCEEDED: { tone: "ok", icon: "check", label: "Succeeded" },
  COMPLETED: { tone: "ok", icon: "check", label: "Completed" },
  PARTIAL: { tone: "warn", icon: "alert", label: "Partial" },
  not_ready: { tone: "warn", icon: "alert", label: "Not ready" },
  unavailable: { tone: "warn", icon: "alert", label: "Unavailable" },
  UNAVAILABLE: { tone: "bad", icon: "x", label: "Unavailable" },
  failed: { tone: "bad", icon: "x", label: "Failed" },
  FAILED: { tone: "bad", icon: "x", label: "Failed" },
  REJECTED: { tone: "bad", icon: "x", label: "Rejected" },
  CANCELED: { tone: "neutral", icon: "x", label: "Canceled" },
  NOT_APPLICABLE: { tone: "neutral", icon: "info", label: "Not applicable" },
  QUEUED: { tone: "neutral", icon: "info", label: "Queued" },
  CREATED: { tone: "neutral", icon: "info", label: "Created" },
  UPLOADING: { tone: "live", icon: "upload", label: "Uploading" },
  VALIDATING: { tone: "live", icon: "pulse", label: "Validating" },
  RUNNING: { tone: "live", icon: "pulse", label: "Running" },
  pending: { tone: "neutral", icon: "info", label: "Checking" },
  // Issue lifecycle (triage decision) — distinct from recheck evidence below.
  OPEN: { tone: "warn", icon: "alert", label: "Open" },
  TRIAGED: { tone: "neutral", icon: "info", label: "Triaged" },
  ACCEPTED_RISK: { tone: "neutral", icon: "shield", label: "Accepted risk" },
  FALSE_POSITIVE: { tone: "neutral", icon: "x", label: "False positive" },
  FIX_PROPOSED: { tone: "live", icon: "wrench", label: "Fix proposed" },
  RESOLVED: { tone: "ok", icon: "check", label: "Resolved" },
  // Recheck evidence from the newest compatible scan.
  VERIFIED_PRESENT: { tone: "warn", icon: "scan", label: "Verified present" },
  VERIFIED_ABSENT: { tone: "ok", icon: "check", label: "Verified absent" },
  NOT_RECHECKED: { tone: "neutral", icon: "info", label: "Not rechecked" },
  UNKNOWN: { tone: "neutral", icon: "info", label: "Unknown" },
  RULE_OBSOLETE: { tone: "neutral", icon: "x", label: "Rule obsolete" },
  // Graph edge classification.
  resolved: { tone: "ok", icon: "check", label: "Resolved" },
  declared: { tone: "neutral", icon: "info", label: "Declared" },
  inferred: { tone: "warn", icon: "sparkles", label: "Inferred" },
  unresolved: { tone: "bad", icon: "x", label: "Unresolved" },
};

/** State indicator that never relies on colour alone: icon + text + tone. */
export function StatusBadge({ state, label }: { state: string; label?: string }) {
  const known = STATES[state] ?? {
    tone: "neutral" as Tone,
    icon: "info" as IconName,
    label: state,
  };
  return (
    <span className={`badge badge-${known.tone}`} data-status={state}>
      <Icon name={known.icon} size={13} />
      {label ?? known.label}
    </span>
  );
}

/** Total findings recorded in a finished scan's summary, or null while unknown. */
export function findingTotal(summary: Record<string, unknown> | null | undefined): number | null {
  const value = summary?.findings;
  return typeof value === "number" ? value : null;
}

export function isTerminalScan(state: string): boolean {
  return ["SUCCEEDED", "PARTIAL", "FAILED", "CANCELED", "BLOCKED", "BUDGET_EXHAUSTED"].includes(
    state,
  );
}
