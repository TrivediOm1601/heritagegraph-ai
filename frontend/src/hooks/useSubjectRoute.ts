import { useCallback, useEffect, useState } from 'react';

/**
 * R6 — the subject route (`?q=<query>`).
 *
 * The app has no router; this is the smallest thing that gives the related
 * topics real navigation:
 *
 * * the URL carries the subject (`/?q=Lalit%20Modi`), so a topic can be
 *   linked, bookmarked and shared;
 * * opening a topic pushes a history entry, so **Back** returns to the
 *   previous subject and **Forward** re-opens it (`popstate`);
 * * submitting the search form only *records* the query in the URL (it does
 *   not fire a second request) — the page drives its own fetch.
 *
 * `open` and `remember` therefore differ in exactly one way: `open` also
 * notifies the page that the subject changed.
 *
 * More than one route is mounted at a time (the search page's and the
 * analysis page's), and each holds its own state, so `open` also announces
 * the subject to every other mounted route through a custom event — a
 * topic clicked on the analysis result opens the search page's subject
 * view instead of only rewriting the URL.
 */

const PARAM = 'q';

/** Event that tells every mounted subject route which subject was opened. */
const OPEN_EVENT = 'heritagegraph:subject-open';

/**
 * id of the element that renders the subject overview (HomePage's search
 * section). A page that opens a subject from somewhere further down the
 * document reveals it, so the overview is actually seen.
 */
export const SUBJECT_SECTION_ID = 'historical-search';

/** Bring the subject overview into view (no-op when the page has none). */
export function revealSubjectSection(): void {
  if (typeof document === 'undefined') return;
  document.getElementById(SUBJECT_SECTION_ID)?.scrollIntoView?.({
    behavior: 'smooth',
    block: 'start',
  });
}

function readQuery(): string | null {
  if (typeof window === 'undefined') return null;
  const value = new URLSearchParams(window.location.search).get(PARAM);
  const trimmed = (value ?? '').trim();
  return trimmed ? trimmed : null;
}

function writeUrl(query: string | null): void {
  if (typeof window === 'undefined') return;
  const url = new URL(window.location.href);
  if (query) url.searchParams.set(PARAM, query);
  else url.searchParams.delete(PARAM);
  const next = `${url.pathname}${url.search}${url.hash}`;
  const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (next === current) return;
  window.history.pushState({ [PARAM]: query }, '', next);
}

/** Tell every mounted subject route (this one included) about an open. */
function announce(query: string): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent<string>(OPEN_EVENT, { detail: query }));
}

export interface SubjectRoute {
  /** The subject named by the current URL (null when there is none). */
  query: string | null;
  /** Open a subject: update the URL *and* ask the page to load it. */
  open: (query: string) => void;
  /** Record the query in the URL without re-running the search. */
  remember: (query: string | null) => void;
}

export function useSubjectRoute(): SubjectRoute {
  const [query, setQuery] = useState<string | null>(() => readQuery());

  useEffect(() => {
    const onPopState = () => setQuery(readQuery());
    const onOpen = (event: Event) => {
      const opened = (event as CustomEvent<string>).detail;
      setQuery(typeof opened === 'string' && opened.trim() ? opened.trim() : readQuery());
    };
    window.addEventListener('popstate', onPopState);
    window.addEventListener(OPEN_EVENT, onOpen);
    return () => {
      window.removeEventListener('popstate', onPopState);
      window.removeEventListener(OPEN_EVENT, onOpen);
    };
  }, []);

  const open = useCallback((next: string) => {
    const trimmed = next.trim();
    if (!trimmed) return;
    writeUrl(trimmed);
    // pushState fires no popstate and every mounted route has its own
    // state, so the open has to be announced for the others to follow.
    announce(trimmed);
  }, []);

  const remember = useCallback((next: string | null) => {
    writeUrl(next && next.trim() ? next.trim() : null);
  }, []);

  return { query, open, remember };
}

export default useSubjectRoute;
