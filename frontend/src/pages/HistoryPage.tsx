import { useCallback, useEffect, useState } from 'react';

import { AnalysisStatus } from '../components/analysis/AnalysisStatus';
import { ExtractedEntities } from '../components/analysis/ExtractedEntities';
import { BriefingPanel } from '../components/briefing/BriefingPanel';
import { KnowledgeGraph } from '../components/graph/KnowledgeGraph';
import { SubjectHeader } from '../components/subject/SubjectHeader';
import { SourceList } from '../components/subject/SourceList';
import type { SourceEntry } from '../components/subject/SourceList';
import {
  ApiError,
  HISTORY_PAGE_SIZE,
  downloadPdf,
  getHistoryDetail,
  historyEntryPdfUrl,
  historyPdfUrl,
  listHistory,
  pdfFilename,
  type HistoryDetail,
  type HistoryItem,
} from '../services/accountApi';
import type { HistoricalBriefing, HistoricalMatch } from '../types/api';

export type HistoryFilter = 'all' | 'text_search' | 'image_analysis';

interface HistoryPageProps {
  /** Reopen an image analysis in the analysis results view. */
  onOpenAnalysis: (analysisId: string) => void;
  /** Reopen a text search as a subject. */
  onOpenSearch: (query: string) => void;
}

type ListState =
  | { phase: 'loading' }
  | { phase: 'error'; message: string }
  | { phase: 'ready'; items: HistoryItem[]; total: number; limit: number; offset: number };

type DetailState =
  | { phase: 'idle' }
  | { phase: 'loading' }
  | { phase: 'error'; message: string }
  | { phase: 'ready'; data: HistoryDetail };

const STATUS_LABELS: Record<string, string> = {
  queued: 'Queued',
  processing: 'Processing',
  completed: 'Completed',
  partial: 'Partial',
  failed: 'Failed',
};

const STATUS_CLASSES: Record<string, string> = {
  queued: 'bg-ivory-200 text-ink-700',
  processing: 'bg-teal-100 text-teal-800',
  completed: 'bg-teal-600 text-ivory',
  partial: 'bg-terracotta-500 text-ivory',
  failed: 'bg-terracotta-600 text-ivory',
};

/** Human-readable date from the backend's ISO timestamp. */
export function formatWhen(value: string): string {
  if (!value) return 'Date not recorded';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return 'Date not recorded';
  return parsed.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

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
      relevance: null,
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
 * R7 — the Analysis history tab.
 *
 * A personal, server-backed history of the signed-in user's text searches and
 * image analyses. Selecting a record shows the **stored** result (never a
 * re-run), with its original status, sources and graph, and states its
 * limitations. Each record (or the whole history) can be exported as a
 * server-generated PDF; the download is only reported as successful once a
 * valid PDF has been received.
 */
export function HistoryPage({ onOpenAnalysis, onOpenSearch }: HistoryPageProps) {
  const [filter, setFilter] = useState<HistoryFilter>('all');
  const [list, setList] = useState<ListState>({ phase: 'loading' });
  const [detail, setDetail] = useState<DetailState>({ phase: 'idle' });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [exporting, setExporting] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exported, setExported] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setList({ phase: 'loading' });
    listHistory({
      limit: HISTORY_PAGE_SIZE,
      offset: 0,
      ...(filter === 'all' ? {} : { kind: filter }),
    }).then(
      (response) => {
        if (!cancelled) {
          setList({
            phase: 'ready',
            items: response.items,
            total: response.total,
            limit: response.limit,
            offset: response.offset,
          });
        }
      },
      (err: unknown) => {
        if (!cancelled) {
          setList({
            phase: 'error',
            message:
              err instanceof ApiError
                ? err.message
                : 'Could not load your history. Please try again.',
          });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [filter, reloadKey]);

  const openEntry = useCallback((entryId: string) => {
    setSelectedId(entryId);
    setDetail({ phase: 'loading' });
    setExportError(null);
    setExported(null);
    getHistoryDetail(entryId).then(
      (data) => setDetail({ phase: 'ready', data }),
      (err: unknown) =>
        setDetail({
          phase: 'error',
          message:
            err instanceof ApiError
              ? err.message
              : 'Could not load that record. Please try again.',
        }),
    );
  }, []);

  const loadMore = async () => {
    if (list.phase !== 'ready') return;
    try {
      const response = await listHistory({
        limit: list.limit,
        offset: list.offset + list.items.length,
        ...(filter === 'all' ? {} : { kind: filter }),
      });
      setList({
        phase: 'ready',
        items: [...list.items, ...response.items],
        total: response.total,
        limit: response.limit,
        offset: list.offset,
      });
    } catch (err) {
      setList({
        phase: 'error',
        message:
          err instanceof ApiError ? err.message : 'Could not load more history. Please try again.',
      });
    }
  };

  const exportOne = async (entryId: string) => {
    setExporting(entryId);
    setExportError(null);
    setExported(null);
    try {
      await downloadPdf(historyEntryPdfUrl(entryId), pdfFilename('analysis'));
      setExported('PDF downloaded.');
    } catch (err) {
      setExportError(
        err instanceof ApiError ? err.message : 'The export failed. Please try again.',
      );
    } finally {
      setExporting(null);
    }
  };

  const exportAll = async () => {
    setExporting('all');
    setExportError(null);
    setExported(null);
    try {
      await downloadPdf(historyPdfUrl(), pdfFilename('history'));
      setExported('History PDF downloaded.');
    } catch (err) {
      setExportError(
        err instanceof ApiError ? err.message : 'The export failed. Please try again.',
      );
    } finally {
      setExporting(null);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <section className="rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="font-display text-2xl text-teal-800">Analysis history</h1>
            <p className="mt-1 text-sm text-ink-500">
              Everything you have searched and analysed, newest first. Reopening a
              record shows the stored result — it never re-runs the AI request.
            </p>
          </div>
          <button
            type="button"
            onClick={exportAll}
            disabled={exporting !== null || list.phase !== 'ready' || list.total === 0}
            className="rounded-lg bg-teal-600 px-3 py-2 text-sm font-medium text-ivory transition-colors hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {exporting === 'all' ? 'Generating PDF…' : 'Export all as PDF'}
          </button>
        </div>

        <div
          role="tablist"
          aria-label="History filter"
          className="mt-4 flex flex-wrap gap-1 rounded-lg bg-ivory-100 p-1"
        >
          {(
            [
              { id: 'all', label: 'All' },
              { id: 'text_search', label: 'Text searches' },
              { id: 'image_analysis', label: 'Image analyses' },
            ] as const
          ).map((option) => (
            <button
              key={option.id}
              type="button"
              role="tab"
              aria-selected={filter === option.id}
              onClick={() => {
                setFilter(option.id);
                setSelectedId(null);
                setDetail({ phase: 'idle' });
              }}
              className={
                filter === option.id
                  ? 'rounded-md bg-teal-600 px-3 py-1.5 text-sm font-medium text-ivory'
                  : 'rounded-md px-3 py-1.5 text-sm font-medium text-ink-700 transition-colors hover:bg-ivory-200'
              }
            >
              {option.label}
            </button>
          ))}
        </div>

        {exportError ? (
          <p
            role="alert"
            className="mt-3 rounded-lg border border-terracotta-300 bg-terracotta-50 px-3 py-2 text-sm text-terracotta-700"
          >
            {exportError}
          </p>
        ) : null}
        {exported ? (
          <p
            role="status"
            className="mt-3 rounded-lg border border-teal-200 bg-teal-50 px-3 py-2 text-sm text-teal-800"
          >
            {exported}
          </p>
        ) : null}
      </section>

      <section aria-label="History records" className="rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm">
        {list.phase === 'loading' ? (
          <p role="status" className="text-sm text-ink-700">
            Loading your history…
          </p>
        ) : null}

        {list.phase === 'error' ? (
          <div role="alert" className="text-sm text-terracotta-700">
            <p>{list.message}</p>
            <button
              type="button"
              onClick={() => setReloadKey((key) => key + 1)}
              className="mt-3 rounded-lg bg-teal-600 px-3 py-2 text-sm font-medium text-ivory hover:bg-teal-700"
            >
              Try again
            </button>
          </div>
        ) : null}

        {list.phase === 'ready' && list.items.length === 0 ? (
          <div role="status" className="rounded-lg border border-dashed border-ivory-300 bg-ivory-50 px-4 py-8 text-center">
            <p className="font-display text-teal-800">Nothing here yet</p>
            <p className="mx-auto mt-1 max-w-md text-sm text-ink-500">
              Run a historical text search or upload an image while signed in, and it will
              appear here with its sources and graph.
            </p>
          </div>
        ) : null}

        {list.phase === 'ready' && list.items.length > 0 ? (
          <>
            <ul className="flex flex-col divide-y divide-ivory-200">
              {list.items.map((item) => (
                <li key={item.id} className="flex flex-wrap items-start justify-between gap-3 py-3">
                  <button
                    type="button"
                    onClick={() => openEntry(item.id)}
                    aria-current={selectedId === item.id ? 'true' : undefined}
                    className={
                      selectedId === item.id
                        ? 'min-w-0 flex-1 rounded-lg bg-ivory-100 px-2 py-1 text-left'
                        : 'min-w-0 flex-1 rounded-lg px-2 py-1 text-left transition-colors hover:bg-ivory-100'
                    }
                  >
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="rounded-full bg-ivory-200 px-2 py-0.5 text-xs font-medium text-ink-700">
                        {item.kind === 'image_analysis' ? 'Image analysis' : 'Text search'}
                      </span>
                      <span
                        className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                          STATUS_CLASSES[item.status] ?? STATUS_CLASSES.queued
                        }`}
                      >
                        {STATUS_LABELS[item.status] ?? item.status}
                      </span>
                      <span className="text-xs text-ink-500">{formatWhen(item.updatedAt)}</span>
                    </span>
                    <span className="mt-1 block truncate font-display text-base text-teal-800">
                      {item.query || 'Untitled record'}
                    </span>
                    {item.summary ? (
                      <span className="mt-0.5 block text-sm text-ink-700">{item.summary}</span>
                    ) : null}
                  </button>
                  <button
                    type="button"
                    onClick={() => exportOne(item.id)}
                    disabled={exporting !== null}
                    className="rounded-lg border border-teal-600 px-3 py-1.5 text-sm font-medium text-teal-700 transition-colors hover:bg-teal-50 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {exporting === item.id ? 'Generating…' : 'Export PDF'}
                  </button>
                </li>
              ))}
            </ul>

            {list.items.length < list.total ? (
              <button
                type="button"
                onClick={loadMore}
                className="mt-4 rounded-lg border border-ivory-300 px-3 py-2 text-sm font-medium text-ink-700 hover:bg-ivory-100"
              >
                Load more ({list.total - list.items.length} remaining)
              </button>
            ) : null}
          </>
        ) : null}
      </section>

      {selectedId ? (
        <section
          aria-label="History record detail"
          className="rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm"
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="font-display text-lg text-teal-800">Stored result</h2>
            <button
              type="button"
              onClick={() => {
                setSelectedId(null);
                setDetail({ phase: 'idle' });
              }}
              className="rounded-lg px-2 py-1 text-sm text-ink-500 hover:bg-ivory-100"
            >
              Close
            </button>
          </div>

          {detail.phase === 'loading' ? (
            <p role="status" className="mt-3 text-sm text-ink-700">
              Loading the stored record…
            </p>
          ) : null}

          {detail.phase === 'error' ? (
            <p role="alert" className="mt-3 text-sm text-terracotta-700">
              {detail.message}
            </p>
          ) : null}

          {detail.phase === 'ready' ? (
            <div className="mt-3 flex flex-col gap-4">
              {detail.data.limitations.length > 0 ? (
                <ul className="list-inside list-disc rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2 text-xs text-ink-500">
                  {detail.data.limitations.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
              ) : null}

              {detail.data.analysis ? (
                <>
                  <AnalysisStatus
                    status={detail.data.analysis.status}
                    progress={detail.data.analysis.progress}
                    warnings={detail.data.analysis.warnings ?? []}
                    error={detail.data.analysis.error}
                  />
                  <ExtractedEntities
                    extractedText={detail.data.analysis.extractedText}
                    interpretation={detail.data.analysis.interpretation}
                    entities={detail.data.analysis.entities ?? []}
                    matches={detail.data.analysis.matches ?? []}
                    briefing={detail.data.analysis.briefing ?? null}
                    onOpenTopic={onOpenSearch}
                  />
                  {detail.data.graph && detail.data.graph.nodes.length > 0 ? (
                    <div className="rounded-xl border border-ivory-300 bg-ivory p-4">
                      <h3 className="font-display text-base text-teal-800">
                        Knowledge graph
                      </h3>
                      <div className="mt-2">
                        <KnowledgeGraph
                          nodes={detail.data.graph.nodes}
                          edges={detail.data.graph.edges}
                        />
                      </div>
                    </div>
                  ) : null}
                  {detail.data.entry.analysisId ? (
                    <button
                      type="button"
                      onClick={() => onOpenAnalysis(detail.data.entry.analysisId!)}
                      className="self-start rounded-lg bg-teal-600 px-3 py-2 text-sm font-medium text-ivory hover:bg-teal-700"
                    >
                      Open in the analysis view
                    </button>
                  ) : null}
                </>
              ) : null}

              {detail.data.search ? (
                <div className="flex flex-col gap-4">
                  <SubjectHeader
                    subject={detail.data.search.briefing?.subject ?? null}
                    query={detail.data.search.query}
                  />
                  {detail.data.search.briefing ? (
                    <BriefingPanel
                      briefing={detail.data.search.briefing}
                      heading="Historical overview (stored)"
                    />
                  ) : null}
                  <SourceList
                    query={detail.data.search.query}
                    sources={toSourceEntries(
                      detail.data.search.briefing ?? null,
                      detail.data.search.results,
                    )}
                  />
                  <button
                    type="button"
                    onClick={() => onOpenSearch(detail.data.search!.query)}
                    className="self-start rounded-lg bg-teal-600 px-3 py-2 text-sm font-medium text-ivory hover:bg-teal-700"
                  >
                    Open this subject
                  </button>
                </div>
              ) : null}
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

export default HistoryPage;
