import type { RelevanceLabel } from '../../types/api';

/**
 * R6 — the supporting sources of a subject overview.
 *
 * One entry per record, deduplicated by URL/title, each showing the record's
 * own excerpt, its publisher and a link to the source. Relevance, when
 * shown, is the backend's coarse retrieval band (never a confidence
 * percentage) — the list itself is evidence *for the answer above*, which is
 * why it sits below the explanation rather than replacing it.
 */

export interface SourceEntry {
  /** Citation/listing number as displayed (1-based). */
  index: number;
  id: string;
  title: string;
  year?: string | null;
  publisher?: string | null;
  url?: string | null;
  excerpt?: string | null;
  relevance?: RelevanceLabel | string | null;
  score?: number | null;
}

export interface SourceListProps {
  /** Query the sources were retrieved for (used in the heading). */
  query: string;
  sources: SourceEntry[];
  heading?: string;
  className?: string;
}

const RELEVANCE_LABELS: Record<string, string> = {
  high: 'High relevance',
  medium: 'Medium relevance',
  low: 'Low relevance',
};

function dedupe(sources: SourceEntry[]): SourceEntry[] {
  const seen = new Set<string>();
  const unique: SourceEntry[] = [];
  for (const source of sources) {
    const key = (source.url || source.title).trim().toLowerCase();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    unique.push(source);
  }
  return unique;
}

export function SourceList({ query, sources, heading, className }: SourceListProps) {
  const unique = dedupe(sources);
  if (unique.length === 0) return null;

  return (
    <section
      aria-labelledby="sources-heading"
      className={`rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm ${className ?? ''}`}
    >
      <h2 id="sources-heading" className="font-display text-lg text-teal-800">
        {heading ?? `Sources for “${query}”`}{' '}
        <span className="text-sm font-normal text-ink-500">
          ({unique.length === 1 ? '1 record' : `${unique.length} records`})
        </span>
      </h2>

      <ol className="mt-3 flex flex-col gap-2">
        {unique.map((source) => (
          <li
            key={source.id || `source-${source.index}`}
            className="rounded-lg border border-ivory-200 bg-ivory-50 px-3 py-2"
          >
            <div className="flex gap-2">
              <span
                aria-hidden="true"
                className="mt-0.5 flex h-5 w-5 flex-none items-center justify-center rounded-full bg-teal-100 text-xs font-medium text-teal-800"
              >
                {source.index}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="font-medium text-ink-900">{source.title}</span>
                  {source.year ? (
                    <span className="text-sm text-terracotta-600">{source.year}</span>
                  ) : null}
                </div>
                {source.excerpt ? (
                  <p className="mt-1 text-sm leading-relaxed text-ink-700">
                    {source.excerpt}
                  </p>
                ) : null}
                <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-500">
                  {source.url ? (
                    <a
                      href={source.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="font-medium text-teal-700 underline underline-offset-2 hover:text-teal-800"
                    >
                      {source.publisher
                        ? `Source ${source.index} — ${source.publisher}`
                        : `Open source ${source.index}`}
                    </a>
                  ) : source.publisher ? (
                    <span>{source.publisher}</span>
                  ) : null}
                  {source.relevance ? (
                    <span
                      title={
                        typeof source.score === 'number'
                          ? `Retrieval relevance ${source.score.toFixed(2)} — how well the query matches this record's text. It is not a confidence that the history is verified.`
                          : undefined
                      }
                    >
                      {RELEVANCE_LABELS[source.relevance] ?? source.relevance}
                    </span>
                  ) : null}
                </div>
              </div>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

export default SourceList;
