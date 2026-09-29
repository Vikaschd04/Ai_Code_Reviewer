import type { Scan } from "../api/client";

/** Human numbering of a project's reviews (scans), oldest = Review 1. */
export function reviewNumbers(scans: Scan[]): Map<string, number> {
  const ordered = [...scans].sort((a, b) => a.created_at.localeCompare(b.created_at));
  return new Map(ordered.map((scan, index) => [scan.id, index + 1]));
}

export function reviewLabel(numbers: Map<string, number>, scanId: string): string {
  const number = numbers.get(scanId);
  return number ? `Review ${String(number)}` : "Review";
}
