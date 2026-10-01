import { Icon, type IconName } from "./Icon";

type Tone = "ok" | "warn" | "bad" | "neutral" | "live";

const STATES: Record<string, { tone: Tone; icon: IconName; label: string }> = {
  ok: { tone: "ok", icon: "check", label: "OK" },
  ready: { tone: "ok", icon: "check", label: "Ready" },
  READY: { tone: "ok", icon: "check", label: "Ready" },
  SUCCEEDED: { tone: "ok", icon: "check", label: "Complete" },
  COMPLETED: { tone: "ok", icon: "check", label: "Completed" },
  PARTIAL: { tone: "warn", icon: "alert", label: "Partly complete" },
  BUDGET_EXHAUSTED: { tone: "warn", icon: "alert", label: "Stopped at limit" },
  // Fix proposals and their validation runs (P05).
  PROPOSED: { tone: "neutral", icon: "wrench", label: "Not checked yet" },
  VALIDATED: { tone: "ok", icon: "check", label: "Checks passed" },
  VALIDATION_FAILED: { tone: "bad", icon: "x", label: "Checks failed" },
  PASSED: { tone: "ok", icon: "check", label: "Passed" },
  // Code reviews of GitHub branches and pull requests (P06).
  CAPTURING: { tone: "live", icon: "download", label: "Getting the code" },
  SCANNING: { tone: "live", icon: "pulse", label: "Reviewing" },
  PUBLISHING: { tone: "live", icon: "upload", label: "Posting to GitHub" },
  SUPERSEDED: { tone: "neutral", icon: "clock", label: "Replaced by a newer commit" },
  SKIPPED: { tone: "neutral", icon: "info", label: "Skipped" },
  not_ready: { tone: "warn", icon: "alert", label: "Not ready" },
  unavailable: { tone: "warn", icon: "alert", label: "Unavailable" },
  UNAVAILABLE: { tone: "bad", icon: "x", label: "Unavailable" },
  failed: { tone: "bad", icon: "x", label: "Failed" },
  FAILED: { tone: "bad", icon: "x", label: "Failed" },
  REJECTED: { tone: "bad", icon: "x", label: "Rejected" },
  CANCELED: { tone: "neutral", icon: "x", label: "Canceled" },
  NOT_APPLICABLE: { tone: "neutral", icon: "info", label: "Not needed" },
  QUEUED: { tone: "neutral", icon: "clock", label: "Waiting" },
  CREATED: { tone: "neutral", icon: "info", label: "Created" },
  UPLOADING: { tone: "live", icon: "upload", label: "Uploading" },
  VALIDATING: { tone: "live", icon: "pulse", label: "Checking upload" },
  RUNNING: { tone: "live", icon: "pulse", label: "In progress" },
  pending: { tone: "neutral", icon: "info", label: "Checking" },
  // Issue lifecycle (triage decision) — distinct from recheck evidence below.
  OPEN: { tone: "warn", icon: "alert", label: "Open" },
  TRIAGED: { tone: "neutral", icon: "users", label: "Acknowledged" },
  ACCEPTED_RISK: { tone: "neutral", icon: "shield", label: "Accepted risk" },
  FALSE_POSITIVE: { tone: "neutral", icon: "x", label: "Not a problem" },
  FIX_PROPOSED: { tone: "live", icon: "wrench", label: "Fix proposed" },
  RESOLVED: { tone: "ok", icon: "check", label: "Resolved" },
  // Recheck evidence from the newest compatible scan.
  VERIFIED_PRESENT: { tone: "warn", icon: "scan", label: "Still present" },
  VERIFIED_ABSENT: { tone: "ok", icon: "check", label: "Fixed" },
  NOT_RECHECKED: { tone: "neutral", icon: "info", label: "Not rechecked" },
  UNKNOWN: { tone: "neutral", icon: "info", label: "Unknown" },
  RULE_OBSOLETE: { tone: "neutral", icon: "x", label: "Rule retired" },
  // Graph edge classification.
  resolved: { tone: "ok", icon: "check", label: "Confirmed" },
  declared: { tone: "neutral", icon: "info", label: "Declared" },
  inferred: { tone: "warn", icon: "sparkles", label: "Likely" },
  unresolved: { tone: "bad", icon: "x", label: "Not found" },
};

/** Compact status: a tinted icon with the label for assistive technology (and as a tooltip). */
export function StatusIcon({ state }: { state: string }) {
  const known = STATES[state] ?? {
    tone: "neutral" as Tone,
    icon: "info" as IconName,
    label: state,
  };
  return (
    <span className={`status-icon status-${known.tone}`} data-status={state} title={known.label}>
      <Icon name={known.icon} size={14} />
      <span className="visually-hidden">{known.label}</span>
    </span>
  );
}

/** Plain-language label of a state (falls back to the raw value). */
export function statusLabel(state: string): string {
  return STATES[state]?.label ?? state;
}

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
