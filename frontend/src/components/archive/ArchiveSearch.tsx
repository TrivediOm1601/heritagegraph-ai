import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';

import { searchArchives } from '../../services/archiveApi';
import { ApiError } from '../../services/api';
import type { ArchiveSearchResponse } from '../../types/archive';

interface ArchiveSearchProps {
  /** When set, results are limited to this archive. */
  archiveId?: string | null;
  /** Pre-fill (and run) a search, e.g. when arriving from the heritage map. */
  initialQuery?: string;
  onOpenFile: (fileId: string) => void;
}

const KIND_OPTIONS = [
  { value: '', label: 'All file types' },
  { value: 'pdf', label: 'PDF' },
  { value: 'docx', label: 'Word' },
  { value: 'pptx', label: 'Slides' },
  { value: 'image', label: 'Image (OCR text)' },
  { value: 'text', label: 'Text / Markdown' },
];

const ENTITY_OPTIONS = [
  { value: '', label: 'Any entity type' },
  { value: 'person', label: 'People' },
  { value: 'place', label: 'Places' },
  { value: 'event', label: 'Events' },
  { value: 'date', label: 'Dates' },
  { value: 'other', label: 'Other candidates' },
];

/** Keyword search over the extracted text, with page-level provenance. */
export function ArchiveSearch({
  archiveId,
  initialQuery,
  onOpenFile,
}: ArchiveSearchProps) {
  const [query, setQuery] = useState(initialQuery?.trim() ?? '');
  const [kind, setKind] = useState('');
  const [language, setLanguage] = useState('');
  const [entityType, setEntityType] = useState('');
  const [state, setState] = useState<
    | { phase: 'idle' }
    | { phase: 'loading' }
    | { phase: 'error'; message: string }
    | { phase: 'ready'; data: ArchiveSearchResponse }
  >({ phase: 'idle' });

  const runSearch = async (term: string) => {
    const trimmed = term.trim();
    if (trimmed.length < 2) {
      setState({ phase: 'error', message: 'Enter at least two characters to search.' });
      return;
    }
    setState({ phase: 'loading' });
    try {
      const data = await searchArchives({
        query: trimmed,
        archiveId: archiveId ?? undefined,
        kind: kind || undefined,
        language: language.trim() || undefined,
        entityType: entityType || undefined,
        limit: 20,
      });
      setState({ phase: 'ready', data });
    } catch (err) {
      setState({
        phase: 'error',
        message: err instanceof ApiError ? err.message : 'The archive search failed.',
      });
    }
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await runSearch(query);
  };

  // Arriving from the heritage map (?q=...): search once, without the user
  // having to retype the site name. Never re-fires on later renders.
  const autoRan = useRef(false);
  useEffect(() => {
    if (autoRan.current) return;
    autoRan.current = true;
    const seed = initialQuery?.trim() ?? '';
    if (seed.length >= 2) void runSearch(seed);
    // Only the initial value matters; later typing is the form's job.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <section aria-labelledby="archive-search-heading" className="flex flex-col gap-3">
      <div>
        <h2 id="archive-search-heading" className="font-display text-lg text-teal-800">
          Search the digitised archive
        </h2>
        <p className="mt-1 text-sm text-ink-500">
          Keyword search over the extracted text of {archiveId ? 'this archive' : 'all your archives'}.
          Results point at the page or slide the passage came from.
        </p>
      </div>

      <form onSubmit={submit} className="flex flex-col gap-3">
        <div className="flex flex-col gap-2 sm:flex-row">
          <label htmlFor="archive-search-input" className="sr-only">
            Search the archive
          </label>
          <input
            id="archive-search-input"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="e.g. stepwell, Rani ki Vav, 1499"
            className="flex-1 rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2 text-sm text-ink-900 placeholder:text-ink-500 focus:border-teal-500 focus:outline-none"
          />
          <button
            type="submit"
            disabled={state.phase === 'loading'}
            className="rounded-lg bg-teal-600 px-4 py-2 text-sm font-medium text-ivory transition-colors hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {state.phase === 'loading' ? 'Searching…' : 'Search archive'}
          </button>
        </div>

        <div className="grid gap-2 sm:grid-cols-3">
          <label className="text-xs font-medium text-ink-700">
            File type
            <select
              value={kind}
              onChange={(event) => setKind(event.target.value)}
              className="mt-1 w-full rounded-lg border border-ivory-300 bg-ivory-50 px-2 py-1.5 text-sm font-normal text-ink-900 focus:border-teal-500 focus:outline-none"
            >
              {KIND_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs font-medium text-ink-700">
            Entity type
            <select
              value={entityType}
              onChange={(event) => setEntityType(event.target.value)}
              className="mt-1 w-full rounded-lg border border-ivory-300 bg-ivory-50 px-2 py-1.5 text-sm font-normal text-ink-900 focus:border-teal-500 focus:outline-none"
            >
              {ENTITY_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs font-medium text-ink-700">
            Language hint
            <input
              value={language}
              onChange={(event) => setLanguage(event.target.value)}
              placeholder="gu, hi, und-Latn"
              className="mt-1 w-full rounded-lg border border-ivory-300 bg-ivory-50 px-2 py-1.5 text-sm font-normal text-ink-900 placeholder:text-ink-500 focus:border-teal-500 focus:outline-none"
            />
          </label>
        </div>
      </form>

      {state.phase === 'error' ? (
        <p role="alert" className="rounded-lg border border-terracotta-300 bg-terracotta-50 px-3 py-2 text-sm text-terracotta-700">
          {state.message}
        </p>
      ) : null}

      {state.phase === 'ready' ? (
        <div className="flex flex-col gap-2">
          {state.data.notice ? (
            <p className="rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2 text-xs text-ink-500">
              {state.data.notice}
            </p>
          ) : null}

          {state.data.results.length === 0 ? (
            <div role="status" className="rounded-lg border border-dashed border-ivory-300 bg-ivory-50 px-3 py-4 text-sm text-ink-500">
              No passage matched “{state.data.query}”. Try a broader term, or remove a filter.
            </div>
          ) : (
            <ol className="flex flex-col gap-2">
              {state.data.results.map((result) => (
                <li
                  key={`${result.fileId}-${result.pageNumber}`}
                  className="rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-3"
                >
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <button
                      type="button"
                      onClick={() => onOpenFile(result.fileId)}
                      className="text-left font-medium text-teal-800 underline-offset-2 hover:underline"
                    >
                      {result.filename}
                    </button>
                    <span className="text-xs text-ink-500">
                      {result.pageLabel} {result.pageNumber} · {result.archiveName}
                    </span>
                  </div>
                  <p className="mt-1 text-sm leading-relaxed text-ink-700">{result.excerpt}</p>
                  {result.matchedEntities.length > 0 ? (
                    <ul className="mt-1 flex flex-wrap gap-1.5">
                      {result.matchedEntities.map((entity) => (
                        <li
                          key={`${entity.name}-${entity.type}`}
                          className="rounded-full bg-ivory-200 px-2 py-0.5 text-xs text-ink-700"
                        >
                          {entity.name} · {entity.type}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </li>
              ))}
            </ol>
          )}
          {state.data.truncated ? (
            <p className="text-xs text-ink-500">
              The archive is large, so only a bounded set of pages was ranked. Narrow the search
              with a filter to reach the rest.
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

export default ArchiveSearch;
