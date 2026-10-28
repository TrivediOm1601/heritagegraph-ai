import { verificationFor } from '../graph/KnowledgeGraph';

/** One piece of supporting evidence to display. */
export interface EvidenceItemView {
  id: string;
  source: string;
  excerpt?: string | null;
  url?: string | null;
}

interface EvidencePanelProps {
  /** Relationship whose evidence is shown (also displayed for verification). */
  relationshipId: string;
  verificationStatus?: string | null;
  summary?: string | null;
  items?: EvidenceItemView[];
  /** Extra uncertainty notes from the backend. */
  notes?: string[];
  loading?: boolean;
  error?: string | null;
  /** Called when the user dismisses the panel. */
  onClose?: () => void;
}

/**
 * A4 — evidence behind the selected relationship.
 *
 * Shows which relationship is being inspected (by id), how it was verified,
 * the supporting evidence items with sources and excerpts, and any notes.
 * Handles the loading and error states of the evidence fetch. All values
 * arrive through props — nothing is hardcoded.
 */
export function EvidencePanel({
  relationshipId,
  verificationStatus,
  summary,
  items,
  notes,
  loading = false,
  error = null,
  onClose,
}: EvidencePanelProps) {
  const verification = verificationFor(verificationStatus);
  const evidenceItems = items ?? [];
  const noteList = notes ?? [];

  return (
    <section
      aria-label="Relationship evidence"
      className="rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-display text-lg text-teal-800">Relationship evidence</h2>
        {onClose ? (
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-ivory-300 px-2.5 py-1 text-xs font-medium text-ink-700 transition-colors hover:bg-ivory-100"
          >
            Close
          </button>
        ) : null}
      </div>

      <p className="mt-1 flex flex-wrap items-center gap-2 text-sm text-ink-500">
        <span>Relationship</span>
        <code
          data-testid="evidence-relationship-id"
          className="rounded bg-ivory-100 px-1.5 py-0.5 font-mono text-xs text-teal-800"
        >
          {relationshipId}
        </code>
        {/* The badge only appears once the backend has actually reported a
            status — an unknown status must never be displayed as a fact. */}
        {verificationStatus ? (
          <span
            data-testid="evidence-verification-status"
            className="rounded-full px-2 py-0.5 text-xs font-semibold"
            style={{ backgroundColor: `${verification.color}22`, color: verification.color }}
          >
            {verification.label}
          </span>
        ) : null}
      </p>

      {loading ? (
        <p role="status" aria-live="polite" className="mt-3 text-sm text-ink-700">
          Loading evidence…
        </p>
      ) : null}

      {error && !loading ? (
        <div
          role="alert"
          className="mt-3 rounded-lg border border-terracotta-300 bg-terracotta-50 px-3 py-2 text-sm text-terracotta-700"
        >
          <span className="font-medium">Could not load the evidence.</span> {error}
        </div>
      ) : null}

      {!loading && !error ? (
        <div className="mt-3 flex flex-col gap-3">
          {summary ? <p className="text-sm text-ink-700">{summary}</p> : null}

          {evidenceItems.length === 0 ? (
            <p className="rounded-lg border border-dashed border-ivory-300 bg-ivory-50 px-3 py-4 text-sm text-ink-500">
              No supporting evidence was recorded for this relationship.
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {evidenceItems.map((item, index) => (
                <li
                  key={item.id || `evidence-${index}`}
                  className="rounded-lg border border-ivory-200 bg-ivory-50 px-3 py-2"
                >
                  <p className="text-sm font-medium text-ink-900">{item.source}</p>
                  {item.excerpt ? (
                    <p className="mt-1 border-l-2 border-terracotta-300 pl-2 text-sm italic text-ink-700">
                      {item.excerpt}
                    </p>
                  ) : null}
                  {item.url ? (
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="mt-1 inline-block text-xs text-teal-700 underline hover:text-teal-800"
                    >
                      Open source
                    </a>
                  ) : null}
                </li>
              ))}
            </ul>
          )}

          {noteList.length > 0 ? (
            <div className="rounded-lg border border-terracotta-200 bg-terracotta-50 px-3 py-2">
              <h3 className="text-sm font-semibold text-terracotta-800">
                {noteList.length === 1 ? 'Note' : `Notes (${noteList.length})`}
              </h3>
              <ul className="mt-1 list-inside list-disc text-sm text-terracotta-700">
                {noteList.map((note, index) => (
                  <li key={`${index}-${note}`}>{note}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

export default EvidencePanel;
