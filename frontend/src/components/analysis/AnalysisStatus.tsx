/** Lifecycle states this component knows how to render. */
export type AnalysisStateView =
  | 'queued'
  | 'processing'
  | 'completed'
  | 'partial'
  | 'failed';

interface AnalysisStatusProps {
  status: AnalysisStateView;
  /** Processing progress from the backend, 0..1, when reported. */
  progress?: number | null;
  /** Non-fatal warnings / uncertainty notes to surface. */
  warnings?: string[];
  /** Failure detail when the analysis itself failed. */
  error?: string | null;
}

const STATUS_PRESENTATION: Record<
  AnalysisStateView,
  { label: string; badgeClass: string }
> = {
  queued: { label: 'Queued', badgeClass: 'bg-ivory-200 text-ivory-900' },
  processing: { label: 'Processing', badgeClass: 'bg-teal-100 text-teal-800' },
  completed: { label: 'Completed', badgeClass: 'bg-teal-600 text-ivory' },
  partial: { label: 'Partial', badgeClass: 'bg-terracotta-500 text-ivory' },
  failed: { label: 'Failed', badgeClass: 'bg-terracotta-600 text-ivory' },
};

/**
 * A3 — status panel for a running or finished analysis.
 *
 * Receives primitives only (no API types), so the provisional contract can
 * change without touching this component. Shows the lifecycle badge, the
 * progress bar when the backend reports progress, backend warnings, and the
 * failure detail for a failed analysis.
 */
export function AnalysisStatus({ status, progress, warnings, error }: AnalysisStatusProps) {
  const presentation = STATUS_PRESENTATION[status];
  const progressPercent =
    typeof progress === 'number' ? Math.round(Math.min(1, Math.max(0, progress)) * 100) : null;

  return (
    <section
      aria-label="Analysis status"
      className="rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm"
    >
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="font-display text-lg text-teal-800">Analysis status</h2>
        <span
          className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${presentation.badgeClass}`}
        >
          {presentation.label}
        </span>
      </div>

      {status === 'processing' || status === 'queued' ? (
        progressPercent !== null ? (
          <div
            role="progressbar"
            aria-label="Analysis progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progressPercent}
            className="mt-3 h-2 w-full overflow-hidden rounded-full bg-ivory-200"
          >
            <div
              className="h-full rounded-full bg-teal-500 transition-all"
              style={{ width: `${progressPercent}%` }}
            />
          </div>
        ) : (
          <p role="status" className="mt-3 text-sm text-ink-700">
            Still working — this usually takes a few moments.
          </p>
        )
      ) : null}

      {status === 'partial' ? (
        <div
          role="status"
          className="mt-3 rounded-lg border border-terracotta-300 bg-terracotta-50 px-3 py-2 text-sm text-terracotta-700"
        >
          <span className="font-medium">Partially complete.</span>{' '}
          Useful results are shown below, but at least one processing stage was
          degraded — read the warnings before relying on them.
        </div>
      ) : null}

      {status === 'failed' ? (
        <div role="alert" className="mt-3 rounded-lg border border-terracotta-300 bg-terracotta-50 px-3 py-2 text-sm text-terracotta-700">
          <span className="font-medium">The analysis failed.</span>{' '}
          {error?.trim() ? error : 'No further detail was provided.'}
        </div>
      ) : null}

      {warnings && warnings.length > 0 ? (
        <div className="mt-3 rounded-lg border border-terracotta-200 bg-terracotta-50 px-3 py-2">
          <h3 className="text-sm font-semibold text-terracotta-800">
            {warnings.length === 1 ? 'Warning' : `Warnings (${warnings.length})`}
          </h3>
          <ul className="mt-1 list-inside list-disc text-sm text-terracotta-700">
            {warnings.map((warning, index) => (
              <li key={`${index}-${warning}`}>{warning}</li>
            ))}
          </ul>
        </div>
      ) : null}

    </section>
  );
}

export default AnalysisStatus;
