import { useImageFallback } from '../../hooks/useImageFallback';
import type { RelatedTopic } from '../../types/api';

/**
 * R6 — related topics as real navigation, not static labels.
 *
 * Each topic renders as an anchor whose `href` is the subject route
 * (`?q=…`), so it works with the keyboard, can be opened in a new tab, and
 * is captured in browser history. A plain left click is intercepted and
 * handed to the page, which loads the topic in place (no full reload) while
 * still pushing the same URL onto the history stack — so Back returns to
 * the previous subject.
 *
 * A topic whose identity the backend could not establish is not rendered:
 * clicking a label that may resolve to the wrong person is worse than not
 * offering it.
 */

export interface RelatedTopicsProps {
  /** Structured topics (identity + query) from the briefing. */
  topics: RelatedTopic[];
  /** Legacy title-only list; used only for topics missing from `topics`. */
  fallbackTitles?: string[];
  /**
   * Called for a normal left click; `href` still works without JS. When the
   * host page passes no handler the anchor simply navigates, so the list is
   * usable anywhere.
   */
  onOpen?: (query: string) => void;
  /**
   * Heading id. Distinct per page, because the search page and the analysis
   * page can both show a topic list at once and ids must stay unique.
   */
  headingId?: string;
  className?: string;
}

const TYPE_LABELS: Record<string, string> = {
  person: 'Person',
  place: 'Place',
  event: 'Event',
  date: 'Date',
  other: 'Subject',
};

export function subjectHref(query: string): string {
  return `?q=${encodeURIComponent(query)}`;
}

function TopicCard({
  topic,
  onOpen,
}: {
  topic: RelatedTopic;
  onOpen?: (query: string) => void;
}) {
  const href = subjectHref(topic.query);
  const typeLabel = topic.type ? TYPE_LABELS[topic.type] ?? topic.type : null;
  // Same-origin proxy first, original URL as the fallback: a thumbnail must
  // not disappear just because one of the two paths is unavailable.
  const { src, handleError } = useImageFallback([
    topic.image?.proxyUrl,
    topic.image?.url,
  ]);

  return (
    <li>
      <a
        href={href}
        onClick={(event) => {
          // Let the browser handle modified clicks (new tab/window).
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          if (event.button !== 0) return;
          // No handler → the anchor navigates normally.
          if (!onOpen) return;
          event.preventDefault();
          onOpen(topic.query);
        }}
        className="group flex h-full gap-3 rounded-lg border border-ivory-300 bg-ivory-50 p-3 transition-colors hover:border-teal-300 hover:bg-teal-50"
        data-testid="related-topic"
      >
        <div className="h-16 w-16 flex-none overflow-hidden rounded-md border border-ivory-200 bg-ivory-100">
          {src ? (
            <img
              src={src}
              alt={topic.image?.altText ?? ''}
              loading="lazy"
              decoding="async"
              onError={handleError}
              className="h-full w-full object-cover"
            />
          ) : (
            <div
              aria-hidden="true"
              className="flex h-full w-full items-center justify-center text-[0.6rem] text-ink-500"
            >
              No image
            </div>
          )}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <span className="font-medium text-ink-900 group-hover:text-teal-800">
              {topic.name}
            </span>
            {typeLabel ? (
              <span className="rounded-full bg-ivory-200 px-1.5 py-0.5 text-[0.65rem] font-medium text-ink-700">
                {typeLabel}
              </span>
            ) : null}
          </div>
          {topic.description ? (
            <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-ink-500">
              {topic.description}
            </p>
          ) : null}
        </div>
      </a>
    </li>
  );
}

export function RelatedTopics({
  topics,
  fallbackTitles = [],
  onOpen,
  headingId = 'related-topics-heading',
  className,
}: RelatedTopicsProps) {
  const covered = new Set(topics.map((topic) => topic.name.toLowerCase()));
  const extraTitles = fallbackTitles.filter(
    (title) => title.trim() && !covered.has(title.trim().toLowerCase()),
  );

  if (topics.length === 0 && extraTitles.length === 0) return null;

  return (
    <section
      aria-labelledby={headingId}
      className={`rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm ${className ?? ''}`}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id={headingId} className="font-display text-lg text-teal-800">
          Related topics
        </h2>
        <span className="text-xs text-ink-500">
          Select a topic to explore it
        </span>
      </div>

      {topics.length > 0 ? (
        <ul className="mt-3 grid gap-3 sm:grid-cols-2">
          {topics.map((topic) => (
            <TopicCard key={topic.id} topic={topic} onOpen={onOpen} />
          ))}
        </ul>
      ) : null}

      {extraTitles.length > 0 ? (
        <div className="mt-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-500">
            Also retrieved
          </h3>
          <ul className="mt-2 flex flex-wrap gap-2">
            {extraTitles.map((title) => (
              <li key={title}>
                <a
                  href={subjectHref(title)}
                  onClick={(event) => {
                    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                    if (event.button !== 0) return;
                    if (!onOpen) return;
                    event.preventDefault();
                    onOpen(title);
                  }}
                  className="inline-block rounded-full border border-ivory-300 bg-ivory-50 px-3 py-1 text-xs text-ink-700 transition-colors hover:border-teal-300 hover:text-teal-800"
                >
                  {title}
                </a>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

export default RelatedTopics;
