import { useEffect, useMemo, useState } from 'react';

import { ApiError } from '../../services/api';
import { listSiteStories } from '../../services/archiveApi';
import type { SiteStoriesResponse, StorySummary } from '../../types/archive';

interface StoryLibraryProps {
  siteId: string;
  siteName: string;
  /** Open one story's dedicated reading view. */
  onOpenStory: (storyId: string) => void;
}

type LibraryState =
  | { phase: 'loading' }
  | { phase: 'error'; message: string }
  | { phase: 'ready'; data: SiteStoriesResponse };

const CATEGORY_TONE: Record<string, string> = {
  history: 'border-teal-300 bg-teal-50 text-teal-900',
  mythology: 'border-terracotta-300 bg-terracotta-50 text-terracotta-800',
  'religious-tradition': 'border-amber-300 bg-amber-50 text-amber-900',
  'local-legend': 'border-stone-300 bg-stone-100 text-stone-800',
  architecture: 'border-slate-300 bg-slate-100 text-slate-800',
  culture: 'border-emerald-300 bg-emerald-50 text-emerald-900',
  'lesser-known': 'border-violet-300 bg-violet-50 text-violet-900',
  'oral-history': 'border-rose-300 bg-rose-50 text-rose-900',
};

/**
 * Stories & Legends section for one curated site.
 *
 * The section is deliberately a library, not a single generated paragraph:
 * each card is one published, sourced record with its category and its
 * epistemic label, and the reader opens inside HeritageGraph AI. A site with
 * no stories says so instead of showing filler.
 */
export function StoryLibrary({ siteId, siteName, onOpenStory }: StoryLibraryProps) {
  const [state, setState] = useState<LibraryState>({ phase: 'loading' });
  const [category, setCategory] = useState('');

  useEffect(() => {
    let cancelled = false;
    setState({ phase: 'loading' });
    setCategory('');
    listSiteStories(siteId).then(
      (data) => {
        if (!cancelled) setState({ phase: 'ready', data });
      },
      (err: unknown) => {
        if (!cancelled) {
          setState({
            phase: 'error',
            message:
              err instanceof ApiError
                ? err.message
                : 'Could not load the stories for this site.',
          });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [siteId]);

  const data = state.phase === 'ready' ? state.data : null;
  const stories = useMemo<StorySummary[]>(() => {
    if (!data) return [];
    return category
      ? data.stories.filter((story) => story.category === category)
      : data.stories;
  }, [data, category]);

  return (
    <section aria-label="Stories and Legends" className="mt-4">
      <div className="flex items-baseline justify-between gap-2">
        <h4 className="font-display text-base font-semibold text-teal-800">
          Stories &amp; Legends
        </h4>
        {data ? (
          <span className="text-xs text-ink-500">
            {data.total} {data.total === 1 ? 'story' : 'stories'}
          </span>
        ) : null}
      </div>

      {state.phase === 'loading' ? (
        <p role="status" className="mt-2 text-xs text-ink-500">
          Loading sourced stories…
        </p>
      ) : null}

      {state.phase === 'error' ? (
        <p role="alert" className="mt-2 rounded-md border border-terracotta-300 bg-terracotta-50 px-2 py-1.5 text-xs text-terracotta-800">
          {state.message}
        </p>
      ) : null}

      {data && data.total === 0 ? (
        <p className="mt-2 rounded-lg border border-dashed border-ivory-300 px-3 py-3 text-xs leading-relaxed text-ink-600">
          No sourced stories have been collected for {siteName} yet. This is not
          a claim that no tradition exists — additional sources are needed
          before a story can be recorded here.
        </p>
      ) : null}

      {data && data.total > 0 ? (
        <>
          {data.categories.length > 1 ? (
            <div className="mt-2 flex flex-wrap gap-1">
              <button
                type="button"
                onClick={() => setCategory('')}
                aria-pressed={category === ''}
                className={`rounded-full border px-2 py-0.5 text-[11px] font-medium transition-colors ${
                  category === ''
                    ? 'border-teal-600 bg-teal-600 text-ivory'
                    : 'border-ivory-300 bg-ivory-50 text-ink-700 hover:bg-ivory-100'
                }`}
              >
                All
              </button>
              {data.categories.map((item) => (
                <button
                  key={item.name}
                  type="button"
                  onClick={() => setCategory(item.name)}
                  aria-pressed={category === item.name}
                  className={`rounded-full border px-2 py-0.5 text-[11px] font-medium transition-colors ${
                    category === item.name
                      ? 'border-teal-600 bg-teal-600 text-ivory'
                      : 'border-ivory-300 bg-ivory-50 text-ink-700 hover:bg-ivory-100'
                  }`}
                >
                  {item.label} ({item.count})
                </button>
              ))}
            </div>
          ) : null}

          <ul className="mt-2 space-y-2">
            {stories.map((story) => (
              <li key={story.id} data-testid={`story-card-${story.id}`}>
                <article className="rounded-lg border border-ivory-300 bg-ivory-50 p-3">
                  <div className="flex flex-wrap items-center gap-1">
                    <span
                      className={`rounded-full border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
                        CATEGORY_TONE[story.category] ??
                        'border-ivory-300 bg-ivory-100 text-ink-700'
                      }`}
                    >
                      {story.categoryLabel}
                    </span>
                    <span className="rounded-full border border-ivory-300 bg-ivory px-1.5 py-0.5 text-[10px] text-ink-600">
                      {story.narrativeTypeLabel}
                    </span>
                    {story.isCommunity ? (
                      <span className="rounded-full border border-terracotta-300 bg-terracotta-50 px-1.5 py-0.5 text-[10px] text-terracotta-800">
                        Community account
                      </span>
                    ) : null}
                  </div>
                  <h5 className="mt-1.5 font-display text-sm font-semibold text-teal-900">
                    {story.title}
                  </h5>
                  <p className="mt-1 text-xs leading-relaxed text-ink-700">{story.summary}</p>
                  <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-500">
                    <span>{story.languageLabel}</span>
                    {story.tradition ? <span>{story.tradition}</span> : null}
                    {story.versionCount > 1 ? (
                      <span>{story.versionCount} alternative versions</span>
                    ) : null}
                    <span>
                      {story.sourceCount} {story.sourceCount === 1 ? 'source' : 'sources'}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => onOpenStory(story.id)}
                    className="mt-2 rounded-md bg-teal-600 px-2.5 py-1 text-xs font-medium text-ivory hover:bg-teal-700"
                  >
                    Read full story
                  </button>
                </article>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[11px] leading-relaxed text-ink-500">
            Labels describe the basis of each account. Mythological and folk
            traditions are presented respectfully and are not ranked against
            documented history.
          </p>
        </>
      ) : null}
    </section>
  );
}

export default StoryLibrary;
