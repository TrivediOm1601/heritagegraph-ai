import { SubjectImage } from './SubjectImage';
import type { SubjectProfile } from '../../types/api';

/**
 * R6 — the subject overview header: what the query is about.
 *
 * Layout: name + type + short description on the left, the subject's
 * photograph on the right (stacking on small screens). Everything shown is
 * source material: the name is the title the source resolved the query to,
 * the description is the source's own one-line summary, and the type is the
 * canonical entity type the source authority reports (omitted when unknown).
 */

const TYPE_LABELS: Record<string, string> = {
  person: 'Person',
  place: 'Place',
  event: 'Event',
  date: 'Date',
  other: 'Subject',
};

export interface SubjectHeaderProps {
  /** The resolved subject, when the backend could profile the query. */
  subject?: SubjectProfile | null;
  /** The query as submitted — the fallback heading when there is no profile. */
  query: string;
  /**
   * Small label above the name. Defaults to "Subject overview"; the analysis
   * page overrides it, because there the subject was *read from an image*
   * and is a candidate the vision model proposed.
   */
  eyebrow?: string;
  className?: string;
}

export function SubjectHeader({ subject, query, eyebrow, className }: SubjectHeaderProps) {
  const name = subject?.name?.trim() || query;
  const typeLabel = subject?.type ? TYPE_LABELS[subject.type] ?? subject.type : null;

  return (
    <header
      className={`rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm sm:p-6 ${className ?? ''}`}
    >
      <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:gap-6">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-terracotta-600">
            {eyebrow ?? 'Subject overview'}
          </p>
          <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h2 className="font-display text-2xl leading-tight text-teal-800 sm:text-3xl">
              {name}
            </h2>
            {typeLabel ? (
              <span className="rounded-full bg-teal-100 px-2 py-0.5 text-xs font-medium text-teal-800">
                {typeLabel}
              </span>
            ) : null}
          </div>

          {subject?.description ? (
            <p className="mt-2 text-sm leading-relaxed text-ink-700">
              {subject.description}
            </p>
          ) : null}

          {subject?.sourceUrl ? (
            <p className="mt-3 text-xs text-ink-500">
              <a
                href={subject.sourceUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium text-teal-700 underline underline-offset-2 hover:text-teal-800"
              >
                Read the source article for “{name}”
              </a>
            </p>
          ) : null}
        </div>

        <SubjectImage
          image={subject?.image ?? null}
          alt={subject?.image?.altText ?? `Photograph of ${name}`}
          className="w-full sm:w-64 sm:flex-none"
        />
      </div>
    </header>
  );
}

export default SubjectHeader;
