import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';

import { BriefingPanel } from '../components/briefing/BriefingPanel';
import { SearchGraphPanel } from '../components/search/SearchGraphPanel';
import { RelatedTopics } from '../components/subject/RelatedTopics';
import { SourceList } from '../components/subject/SourceList';
import type { SourceEntry } from '../components/subject/SourceList';
import { SubjectHeader } from '../components/subject/SubjectHeader';
import { SUBJECT_SECTION_ID, useSubjectRoute } from '../hooks/useSubjectRoute';
import { ApiError, MIN_SEARCH_QUERY_LENGTH, searchHistoricalRecords } from '../services/api';
import type { HistoricalBriefing, HistoricalMatch } from '../types/api';

/**
 * How-it-works cards shown under the hero (moved here from App.tsx in A5).
 */
const CAPABILITIES = [
  {
    step: '01',
    title: 'Upload or search',
    body: 'Drop in a photograph, scan or manuscript page, or start from a text search to trigger an analysis.',
  },
  {
    step: '02',
    title: 'Review extracted entities',
    body: 'People, places, events and dates found in the image are listed with progress and error status while processing runs.',
  },
  {
    step: '03',
    title: 'Explore the graph',
    body: 'Entities become an interactive knowledge graph, each connection backed by evidence and a verification status.',
  },
];

type SearchState =
  | { phase: 'idle' }
  | { phase: 'loading' }
  | { phase: 'error'; message: string }
  | {
      phase: 'ready';
      query: string;
      results: HistoricalMatch[];
      briefing: HistoricalBriefing | null;
      /** Backend notice: the answer is thin because the source failed. */
      notice: string | null;
    };

/**
 * The sources shown under a subject: the briefing's cited sources when the
 * backend sent a briefing (they carry the citation numbers the facts refer
 * to), otherwise the raw retrieval records.
 */
function toSourceEntries(
  briefing: HistoricalBriefing | null,
  results: HistoricalMatch[],
): SourceEntry[] {
  if (briefing && briefing.sources.length > 0) {
    return briefing.sources.map((source) => ({
      index: source.index,
      id: source.id,
      title: source.title,
      publisher: source.publisher,
      url: source.url,
      excerpt: source.excerpt,
      relevance: typeof source.relevance === 'number' ? null : null,
      score: source.relevance ?? null,
    }));
  }
  return results.map((result, index) => ({
    index: index + 1,
    id: result.id || `result-${index}`,
    title: result.title,
    year: result.year,
    publisher: result.source,
    url: result.url,
    excerpt: result.excerpt,
    relevance: result.relevance ?? null,
    score: result.score ?? null,
  }));
}

/**
 * A5/R6 — the home page: hero, how-it-works, and the historical search.
 *
 * The search keeps its failure state strictly separate from an empty result:
 * a backend/network error renders a `role="alert"` error box, while a
 * successful search that matched nothing renders a neutral `role="status"`
 * empty state. Invalid input is rejected in the browser before any request.
 *
 * R6 adds the subject overview (photograph + description + key facts),
 * clickable related topics, and the subject route: the current query lives
 * in `?q=`, so opening a topic navigates, Back returns to the previous
 * subject, and every subject view is directly linkable.
 */
export function HomePage() {
  const [term, setTerm] = useState('');
  const [search, setSearch] = useState<SearchState>({ phase: 'idle' });
  // R7: the results have two views. "Knowledge graph" is mounted lazily so
  // the graph request is only made when the user asks for it.
  const [resultView, setResultView] = useState<'overview' | 'graph'>('overview');
  const { query: routeQuery, open, remember } = useSubjectRoute();
  const requestIdRef = useRef(0);

  const runSearch = useCallback(async (rawQuery: string) => {
    const query = rawQuery.trim();
    if (query.length < MIN_SEARCH_QUERY_LENGTH) {
      setSearch({
        phase: 'error',
        message: `Enter at least ${MIN_SEARCH_QUERY_LENGTH} characters to search.`,
      });
      return;
    }

    // A slow first request must never overwrite a newer subject's result.
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setSearch({ phase: 'loading' });

    try {
      const response = await searchHistoricalRecords(query);
      if (requestIdRef.current !== requestId) return;
      setSearch({
        phase: 'ready',
        query: response.query,
        results: response.results,
        briefing: response.briefing ?? null,
        notice: response.notice ?? null,
      });
      // A new subject always opens on the overview, never on the previous
      // search's graph.
      setResultView('overview');
      setTerm(query);
    } catch (err) {
      if (requestIdRef.current !== requestId) return;
      setSearch({
        phase: 'error',
        message:
          err instanceof ApiError
            ? err.message
            : 'Something went wrong while searching. Please try again.',
      });
    }
  }, []);

  // The URL is the only thing that changes when a topic is opened (here or
  // on the analysis page) or the browser navigates back, so this is what
  // drives the fetch. The search field follows the subject on screen too —
  // a topic opened from an analysis result must show in the input, not just
  // in the results below it.
  useEffect(() => {
    if (!routeQuery) return;
    setTerm(routeQuery);
    void runSearch(routeQuery);
  }, [routeQuery, runSearch]);

  // Navigating back past the first subject (or to a cleared URL) returns to
  // the page's prompt rather than leaving the last subject on screen.
  useEffect(() => {
    const onPopState = () => {
      if (!new URLSearchParams(window.location.search).get('q')) {
        setSearch({ phase: 'idle' });
      }
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const query = term.trim();
    if (query.length < MIN_SEARCH_QUERY_LENGTH) {
      setSearch({
        phase: 'error',
        message: `Enter at least ${MIN_SEARCH_QUERY_LENGTH} characters to search.`,
      });
      return;
    }
    // Record the subject in the URL, then search once (no effect loop).
    remember(query);
    await runSearch(query);
  };

  const openTopic = useCallback(
    (query: string) => {
      setTerm(query);
      open(query);
    },
    [open],
  );

  return (
    <div>
      <section className="rounded-2xl border border-ivory-300 bg-ivory-100 p-6 shadow-sm sm:p-10">
        <p className="text-xs font-semibold uppercase tracking-[0.25em] text-terracotta-600">
          HeritageGraph AI
        </p>
        <h1 className="mt-3 max-w-3xl font-display text-3xl leading-tight text-teal-800 sm:text-5xl">
          Every image holds a story.{' '}
          <span className="text-terracotta-600">We map its connections.</span>
        </h1>
        <p className="mt-4 max-w-2xl text-base leading-relaxed text-ink-700 sm:text-lg">
          Upload a historical image or search with text, and HeritageGraph AI extracts
          the people, places and events it contains, matches them against historical
          records, and lays the relationships out as a graph you can verify
          evidence-by-evidence.
        </p>
      </section>

      <section
        aria-label="How it works"
        className="mt-6 grid gap-4 sm:mt-8 sm:grid-cols-3 sm:gap-6"
      >
        {CAPABILITIES.map((item) => (
          <article
            key={item.step}
            className="rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm"
          >
            <p className="font-display text-sm font-semibold tracking-widest text-terracotta-500">
              {item.step}
            </p>
            <h2 className="mt-2 font-display text-lg text-teal-800">{item.title}</h2>
            <p className="mt-2 text-sm leading-relaxed text-ink-700">{item.body}</p>
          </article>
        ))}
      </section>

      {/* A5/R6 — text search over historical records */}
      <section
        id={SUBJECT_SECTION_ID}
        aria-labelledby="search-heading"
        className="mt-6 scroll-mt-4 rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm sm:mt-8 sm:p-6"
      >
        <h2 id="search-heading" className="font-display text-lg text-teal-800">
          Search historical records
        </h2>
        <p className="mt-1 text-sm text-ink-500">
          Look up a person, place, event or year — results come straight from the
          archive, with the sources’ own wording quoted underneath.
        </p>

        <form onSubmit={handleSubmit} className="mt-4 flex flex-col gap-3 sm:flex-row">
          <label htmlFor="historical-search-input" className="sr-only">
            Search terms
          </label>
          <input
            id="historical-search-input"
            type="search"
            value={term}
            onChange={(event) => setTerm(event.target.value)}
            placeholder="e.g. Rani ki Vav, Qutub Minar, 1946"
            className="flex-1 rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2 text-sm text-ink-900 placeholder:text-ink-500 focus:border-teal-500 focus:outline-none"
            autoComplete="off"
          />
          <button
            type="submit"
            disabled={search.phase === 'loading'}
            className="rounded-lg bg-teal-600 px-4 py-2 text-sm font-medium text-ivory transition-colors hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {search.phase === 'loading' ? 'Searching…' : 'Search'}
          </button>
        </form>

        <div className="mt-4">
          {search.phase === 'idle' ? (
            <p className="text-sm text-ink-500">
              Search by person, place, event or year to see matching records.
            </p>
          ) : null}

          {search.phase === 'loading' ? (
            <p role="status" aria-live="polite" className="text-sm text-ink-700">
              Searching the archive…
            </p>
          ) : null}

          {/* Failures: an error, never silently mistaken for "no results". */}
          {search.phase === 'error' ? (
            <div
              role="alert"
              className="rounded-lg border border-terracotta-300 bg-terracotta-50 px-3 py-2 text-sm text-terracotta-700"
            >
              <span className="font-medium">Search failed.</span> {search.message}
            </div>
          ) : null}

          {/* Successful search: the subject overview, or a neutral empty state. */}
          {search.phase === 'ready' ? (
            search.results.length === 0 && !search.briefing ? (
              <div
                role="status"
                className="rounded-lg border border-dashed border-ivory-300 bg-ivory-50 px-3 py-4 text-sm text-ink-500"
              >
                <p className="font-medium text-ink-700">
                  No relevant historical records were found for “{search.query}”.
                </p>
                <p className="mt-1">
                  The retrieval found no sufficiently relevant source, so no
                  explanation is shown. Try a more specific name, place or year —
                  or check the spelling.
                </p>
                {search.notice ? (
                  <p
                    className="mt-2 text-xs text-terracotta-700"
                    data-testid="search-notice"
                  >
                    {search.notice}
                  </p>
                ) : null}
              </div>
            ) : (
              <div className="flex flex-col gap-4" data-testid="subject-overview">
                {/* A degraded source is disclosed, never hidden behind a
                    normal-looking result set. */}
                {search.notice ? (
                  <p
                    role="status"
                    className="rounded-lg border border-terracotta-200 bg-terracotta-50 px-3 py-2 text-xs text-terracotta-700"
                    data-testid="search-notice"
                  >
                    {search.notice}
                  </p>
                ) : null}

                {/* R7: switch between the sourced overview and the graph of
                    the same retrieval. The overview keeps the sections this
                    page already had (subject, explanation, topics, sources). */}
                <div
                  role="tablist"
                  aria-label="Result views"
                  className="flex gap-1 rounded-lg bg-ivory-100 p-1"
                >
                  {(
                    [
                      { id: 'overview', label: 'Overview' },
                      { id: 'graph', label: 'Knowledge graph' },
                    ] as const
                  ).map((tab) => (
                    <button
                      key={tab.id}
                      type="button"
                      role="tab"
                      aria-selected={resultView === tab.id}
                      onClick={() => setResultView(tab.id)}
                      className={
                        resultView === tab.id
                          ? 'flex-1 rounded-md bg-teal-600 px-3 py-1.5 text-sm font-medium text-ivory'
                          : 'flex-1 rounded-md px-3 py-1.5 text-sm font-medium text-ink-700 transition-colors hover:bg-ivory-200'
                      }
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>

                {resultView === 'graph' ? (
                  <SearchGraphPanel query={search.query} onOpenTopic={openTopic} />
                ) : (
                  <>
                    {/* 1. What the query is about, with its photograph. */}
                    <SubjectHeader
                      subject={search.briefing?.subject ?? null}
                      query={search.query}
                    />

                    {/* 2. The historical explanation, quoted from the sources. */}
                    {search.briefing ? (
                      <BriefingPanel
                        briefing={search.briefing}
                        heading="Historical overview"
                      />
                    ) : null}

                    {/* 3. Topics the user can open, each with its own identity. */}
                    {search.briefing ? (
                      <RelatedTopics
                        topics={search.briefing.relatedTopics}
                        fallbackTitles={search.briefing.related}
                        onOpen={openTopic}
                      />
                    ) : null}

                    {/* 4. The sources supporting the explanation above. */}
                    <SourceList
                      query={search.query}
                      sources={toSourceEntries(search.briefing, search.results)}
                    />
                  </>
                )}
              </div>
            )
          ) : null}
        </div>
      </section>
    </div>
  );
}

export default HomePage;
