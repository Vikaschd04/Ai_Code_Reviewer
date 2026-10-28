import type { WorkspaceFixResult } from "../api/client";

// "Fix automatically" on an NFR checkpoint applies the fixes, then opens the workspace; the result
// (what was fixed, what was skipped and why, whether the check started) is shown there once.
// Session storage is only a convenience: without it the workspace still shows its changes.

function key(workspaceId: string): string {
  return `rx-fix-result:${workspaceId}`;
}

export function saveFixNote(workspaceId: string, result: WorkspaceFixResult): void {
  try {
    window.sessionStorage.setItem(key(workspaceId), JSON.stringify(result));
  } catch {
    // storage unavailable
  }
}

/** Read the note (render may run twice in development, so reading does not remove it). */
export function readFixNote(workspaceId: string): WorkspaceFixResult | null {
  try {
    const raw = window.sessionStorage.getItem(key(workspaceId));
    return raw ? (JSON.parse(raw) as WorkspaceFixResult) : null;
  } catch {
    return null;
  }
}

/** Forget the note once shown, so it appears only once. */
export function clearFixNote(workspaceId: string): void {
  try {
    window.sessionStorage.removeItem(key(workspaceId));
  } catch {
    // storage unavailable
  }
}
