/**
 * A3 — shared display formatters.
 *
 * Pure functions only: no network, no React. `formatBytes` moved here from
 * services/api.ts (A2); that module re-exports it so existing imports keep
 * working.
 */

/** Formats a byte count for user-facing messages. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Formats a 0..1 score as a percentage for display (e.g. 0.85 → "85%").
 * Missing or invalid values render as an em dash rather than "0%".
 */
export function formatConfidence(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const clamped = Math.min(1, Math.max(0, value));
  return `${Math.round(clamped * 100)}%`;
}

/** Truncates text to a maximum length, appending an ellipsis when cut. */
export function truncateText(text: string, maxLength: number): string {
  if (maxLength <= 0) return '';
  if (text.length <= maxLength) return text;
  return `${text.slice(0, Math.max(0, maxLength - 1))}…`;
}

/** "3 entities" / "1 entity" style counts. */
export function pluralizeCount(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}
