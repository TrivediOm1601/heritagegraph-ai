import { useCallback, useEffect, useRef, useState } from 'react';

import { ArchiveFileDetailPanel } from '../components/archive/ArchiveFileDetail';
import { ArchiveFileTable } from '../components/archive/ArchiveFileTable';
import { CreateArchiveForm, ArchiveUploadPanel } from '../components/archive/ArchiveIntake';
import { ArchiveSearch } from '../components/archive/ArchiveSearch';
import { ArchiveTimeline } from '../components/archive/ArchiveTimeline';
import { isBusy } from '../components/archive/ArchiveStatus';
import {
  archiveReportFilename,
  archiveReportUrl,
  createArchive,
  deleteArchive,
  deleteArchiveFile,
  getArchive,
  getArchiveFile,
  getArchiveTimeline,
  listArchives,
  renameArchive,
  reprocessArchiveFile,
} from '../services/archiveApi';
import type { CreateArchiveInput } from '../services/archiveApi';
import { ApiError, downloadPdf } from '../services/accountApi';
import type {
  Archive,
  ArchiveDetail,
  ArchiveFileDetail,
  ArchiveUploadResult,
  TimelineResponse,
} from '../types/archive';

function readArchiveFromUrl(): string | null {
  if (typeof window === 'undefined') return null;
  return new URLSearchParams(window.location.search).get('archive');
}

function readQueryFromUrl(): string {
  if (typeof window === 'undefined') return '';
  return new URLSearchParams(window.location.search).get('q') ?? '';
}

function pushArchiveUrl(archiveId: string | null) {
  if (typeof window === 'undefined') return;
  const url = new URL(window.location.href);
  if (archiveId) url.searchParams.set('archive', archiveId);
  else url.searchParams.delete('archive');
  url.hash = 'archive';
  window.history.pushState(
    { archive: archiveId },
    '',
    `${url.pathname}${url.search}${url.hash}`,
  );
}

/**
 * Heritage Archive Studio.
 *
 * The whole workflow lives here: create an archive, upload images/documents/
 * ZIPs, watch each file's real processing status, read the extracted pages,
 * search the archive, and open the dated events. The selected archive is in
 * `?archive=…`, so a refresh or a shared link reopens the same one.
 */
export function ArchivePage() {
  const [archives, setArchives] = useState<Archive[]>([]);
  const [listPhase, setListPhase] = useState<'loading' | 'error' | 'ready'>('loading');
  const [listError, setListError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(readArchiveFromUrl);
  const [archive, setArchive] = useState<ArchiveDetail | null>(null);
  const [archivePhase, setArchivePhase] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [archiveError, setArchiveError] = useState<string | null>(null);
  const [fileDetail, setFileDetail] = useState<ArchiveFileDetail | null>(null);
  const [filePhase, setFilePhase] = useState<'idle' | 'loading' | 'error'>('idle');
  const [fileError, setFileError] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<TimelineResponse | null>(null);
  const [actionBusyId, setActionBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [draftName, setDraftName] = useState('');
  const [reloadKey, setReloadKey] = useState(0);
  const archiveRequest = useRef(0);

  const loadArchive = useCallback(async (archiveId: string, silent = false) => {
    const requestId = archiveRequest.current + 1;
    archiveRequest.current = requestId;
    if (!silent) setArchivePhase('loading');
    try {
      const [detail, events] = await Promise.all([
        getArchive(archiveId),
        getArchiveTimeline(archiveId).catch(() => null),
      ]);
      if (archiveRequest.current !== requestId) return;
      setArchive(detail);
      setTimeline(events);
      setArchivePhase('ready');
      setArchiveError(null);
    } catch (err) {
      if (archiveRequest.current !== requestId) return;
      setArchiveError(err instanceof ApiError ? err.message : 'Could not load the archive.');
      setArchivePhase('error');
    }
  }, []);

  const refreshList = useCallback(async () => {
    try {
      const items = await listArchives();
      setArchives(items);
      setListPhase('ready');
      setListError(null);
    } catch (err) {
      setListPhase('error');
      setListError(err instanceof ApiError ? err.message : 'Could not load your archives.');
    }
  }, []);

  useEffect(() => {
    void refreshList();
  }, [refreshList, reloadKey]);

  useEffect(() => {
    if (!selectedId) {
      setArchive(null);
      setTimeline(null);
      setFileDetail(null);
      setArchivePhase('idle');
      return;
    }
    setArchive(null);
    setFileDetail(null);
    setFilePhase('idle');
    void loadArchive(selectedId);
  }, [selectedId, loadArchive, reloadKey]);

  // Back/Forward between archives (and the app's hash navigation) restores
  // the archive the URL names, not the last one on screen.
  useEffect(() => {
    const onPopState = () => setSelectedId(readArchiveFromUrl());
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  // Poll while files are being processed: an upload must resolve into a real
  // final state without a manual refresh, and polling stops as soon as it can.
  useEffect(() => {
    if (!selectedId || !archive) return;
    const busy = archive.files.some((file) => isBusy(file.status));
    if (!busy) return;
    const timer = window.setInterval(() => {
      void loadArchive(selectedId, true);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [archive, selectedId, loadArchive]);

  useEffect(() => {
    if (!fileDetail || !isBusy(fileDetail.file.status)) return;
    const timer = window.setInterval(() => {
      void openFile(fileDetail.file.id, { silent: true });
    }, 2500);
    return () => window.clearInterval(timer);
    // openFile is stable; intentionally not in the dependency list.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileDetail]);

  const openArchive = (archiveId: string) => {
    setSelectedId(archiveId);
    pushArchiveUrl(archiveId);
  };

  const handleCreate = async (input: CreateArchiveInput) => {
    const created = await createArchive(input);
    setNotice(`Archive “${created.name}” created.`);
    setError(null);
    await refreshList();
    openArchive(created.id);
  };

  const handleUploaded = (result: ArchiveUploadResult) => {
    setNotice(result.message);
    setError(null);
    if (selectedId) void loadArchive(selectedId, true);
    void refreshList();
  };

  const openFile = async (fileId: string, options: { silent?: boolean } = {}) => {
    if (!options.silent) {
      setFilePhase('loading');
      setFileError(null);
    }
    try {
      const detail = await getArchiveFile(fileId);
      setFileDetail(detail);
      setFilePhase('idle');
      if (!options.silent) {
        window.setTimeout(() => {
          document.getElementById('archive-file-detail')?.scrollIntoView?.({
            behavior: 'smooth',
            block: 'start',
          });
        }, 0);
      }
    } catch (err) {
      setFileError(err instanceof ApiError ? err.message : 'Could not load that file.');
      setFilePhase('error');
    }
  };

  const handleReprocess = async (fileId: string) => {
    setActionBusyId(fileId);
    setError(null);
    try {
      await reprocessArchiveFile(fileId);
      setNotice('The file was queued for reprocessing.');
      if (selectedId) void loadArchive(selectedId, true);
      if (fileDetail?.file.id === fileId) void openFile(fileId, { silent: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not reprocess the file.');
    } finally {
      setActionBusyId(null);
    }
  };

  const handleDeleteFile = async (fileId: string) => {
    if (!window.confirm('Remove this file from the archive? The original is kept on the server.')) {
      return;
    }
    setActionBusyId(fileId);
    setError(null);
    try {
      await deleteArchiveFile(fileId);
      if (fileDetail?.file.id === fileId) setFileDetail(null);
      setNotice('The file was removed from the archive.');
      if (selectedId) void loadArchive(selectedId, true);
      void refreshList();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove the file.');
    } finally {
      setActionBusyId(null);
    }
  };

  const handleRename = async () => {
    if (!archive) return;
    const name = draftName.trim();
    if (!name) return;
    try {
      await renameArchive(archive.id, { name });
      setRenaming(false);
      setNotice('Archive renamed.');
      await loadArchive(archive.id);
      await refreshList();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not rename the archive.');
    }
  };

  const handleDeleteArchive = async () => {
    if (!archive) return;
    if (!window.confirm(`Remove “${archive.name}” from your workspace?`)) return;
    try {
      await deleteArchive(archive.id);
      setSelectedId(null);
      setArchive(null);
      pushArchiveUrl(null);
      setNotice('Archive removed.');
      await refreshList();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove the archive.');
    }
  };

  const handleReport = async () => {
    if (!archive) return;
    setError(null);
    try {
      await downloadPdf(
        archiveReportUrl(archive.id),
        archiveReportFilename(archive.name),
      );
      setNotice('Archive report downloaded.');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The report could not be generated.');
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <section className="rounded-2xl border border-ivory-300 bg-ivory-100 p-6 shadow-sm sm:p-8">
        <p className="text-xs font-semibold uppercase tracking-[0.25em] text-terracotta-600">
          Heritage Archive Studio
        </p>
        <h1 className="mt-2 max-w-3xl font-display text-3xl leading-tight text-teal-800 sm:text-4xl">
          Turn disconnected documents into a searchable archive
        </h1>
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-ink-700 sm:text-base">
          Upload photographs, scanned reports, Word documents, slide decks or a ZIP of field
          notes. Each file is digitised independently, its text becomes searchable by page, and
          the dated passages become a timeline you can trace back to the source.
        </p>
      </section>

      {error ? (
        <p role="alert" className="rounded-lg border border-terracotta-300 bg-terracotta-50 px-3 py-2 text-sm text-terracotta-700">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p role="status" className="rounded-lg border border-teal-200 bg-teal-50 px-3 py-2 text-sm text-teal-900">
          {notice}
        </p>
      ) : null}

      <section className="rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm">
        <ArchiveSearch
          archiveId={selectedId}
          initialQuery={readQueryFromUrl()}
          onOpenFile={openFile}
        />
      </section>

      <div className="grid gap-6 lg:grid-cols-[19rem_minmax(0,1fr)]">
        <aside className="flex flex-col gap-4">
          <section aria-labelledby="archive-list-heading" className="rounded-xl border border-ivory-300 bg-ivory p-4 shadow-sm">
            <h2 id="archive-list-heading" className="font-display text-lg text-teal-800">
              Your archives
            </h2>
            {listPhase === 'loading' ? (
              <p role="status" className="mt-2 text-sm text-ink-500">
                Loading your archives…
              </p>
            ) : null}
            {listPhase === 'error' ? (
              <div role="alert" className="mt-2 text-sm text-terracotta-700">
                <p>{listError}</p>
                <button
                  type="button"
                  onClick={() => setReloadKey((key) => key + 1)}
                  className="mt-2 rounded-md border border-terracotta-300 px-2 py-1 text-xs font-medium"
                >
                  Try again
                </button>
              </div>
            ) : null}
            {listPhase === 'ready' && archives.length === 0 ? (
              <p className="mt-2 text-sm text-ink-500">
                No archives yet. Create the first one below.
              </p>
            ) : null}
            {archives.length > 0 ? (
              <ul className="mt-3 flex flex-col gap-1">
                {archives.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      onClick={() => openArchive(item.id)}
                      aria-current={item.id === selectedId ? 'true' : undefined}
                      className={
                        item.id === selectedId
                          ? 'w-full rounded-lg bg-teal-600 px-3 py-2 text-left text-ivory'
                          : 'w-full rounded-lg px-3 py-2 text-left transition-colors hover:bg-ivory-100'
                      }
                    >
                      <span className="block truncate text-sm font-medium">{item.name}</span>
                      <span
                        className={
                          item.id === selectedId
                            ? 'mt-0.5 block text-xs text-teal-100'
                            : 'mt-0.5 block text-xs text-ink-500'
                        }
                      >
                        {item.stats.files} file(s) · {item.stats.entities} entit
                        {item.stats.entities === 1 ? 'y' : 'ies'}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
          </section>

          <section aria-labelledby="archive-create-heading" className="rounded-xl border border-ivory-300 bg-ivory p-4 shadow-sm">
            <h2 id="archive-create-heading" className="font-display text-lg text-teal-800">
              New archive
            </h2>
            <p className="mt-1 text-xs text-ink-500">
              A collection with its own files, search and timeline.
            </p>
            <div className="mt-3">
              <CreateArchiveForm onCreate={handleCreate} />
            </div>
          </section>
        </aside>

        <div className="flex min-w-0 flex-col gap-5">
          {!selectedId ? (
            <section className="rounded-xl border border-dashed border-ivory-300 bg-ivory p-8 text-center">
              <p className="font-display text-xl text-teal-800">Select or create an archive</p>
              <p className="mx-auto mt-2 max-w-md text-sm text-ink-500">
                Everything you upload belongs to an archive, so its files, extracted pages,
                entities and dated events stay together.
              </p>
            </section>
          ) : archivePhase === 'loading' && !archive ? (
            <p role="status" className="rounded-xl border border-ivory-300 bg-ivory p-5 text-sm text-ink-700">
              Loading the archive…
            </p>
          ) : archivePhase === 'error' && !archive ? (
            <div role="alert" className="rounded-xl border border-terracotta-300 bg-terracotta-50 p-5 text-sm text-terracotta-800">
              <p>{archiveError}</p>
              <button
                type="button"
                onClick={() => selectedId && loadArchive(selectedId)}
                className="mt-3 rounded-lg bg-teal-600 px-3 py-2 text-sm font-medium text-ivory hover:bg-teal-700"
              >
                Try again
              </button>
            </div>
          ) : archive ? (
            <>
              <section className="rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    {renaming ? (
                      <div className="flex flex-wrap items-center gap-2">
                        <label htmlFor="archive-rename" className="sr-only">
                          Archive name
                        </label>
                        <input
                          id="archive-rename"
                          value={draftName}
                          onChange={(event) => setDraftName(event.target.value)}
                          className="rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-1.5 text-sm text-ink-900 focus:border-teal-500 focus:outline-none"
                        />
                        <button
                          type="button"
                          onClick={handleRename}
                          className="rounded-lg bg-teal-600 px-3 py-1.5 text-sm font-medium text-ivory hover:bg-teal-700"
                        >
                          Save
                        </button>
                        <button
                          type="button"
                          onClick={() => setRenaming(false)}
                          className="rounded-lg px-2 py-1.5 text-sm text-ink-500 hover:bg-ivory-100"
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <h2 className="break-words font-display text-2xl text-teal-800">
                        {archive.name}
                      </h2>
                    )}
                    <p className="mt-1 text-sm text-ink-500">
                      {[archive.region, archive.description].filter(Boolean).join(' · ')}
                    </p>
                    {archive.tags.length > 0 ? (
                      <ul className="mt-2 flex flex-wrap gap-1.5">
                        {archive.tags.map((tag) => (
                          <li
                            key={tag}
                            className="rounded-full bg-ivory-200 px-2 py-0.5 text-xs text-ink-700"
                          >
                            {tag}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        setDraftName(archive.name);
                        setRenaming(true);
                      }}
                      className="rounded-lg border border-ivory-300 px-3 py-1.5 text-sm font-medium text-ink-700 hover:bg-ivory-100"
                    >
                      Rename
                    </button>
                    <button
                      type="button"
                      onClick={handleReport}
                      className="rounded-lg border border-teal-600 px-3 py-1.5 text-sm font-medium text-teal-700 hover:bg-teal-50"
                    >
                      Download report
                    </button>
                    <button
                      type="button"
                      onClick={handleDeleteArchive}
                      className="rounded-lg px-3 py-1.5 text-sm font-medium text-terracotta-700 hover:bg-terracotta-50"
                    >
                      Remove
                    </button>
                  </div>
                </div>

                <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
                  {[
                    { label: 'Files', value: archive.stats.files },
                    { label: 'Completed', value: archive.stats.completed },
                    { label: 'Needs review', value: archive.stats.partial + archive.stats.failed },
                    { label: 'Pages', value: archive.stats.pages },
                    { label: 'Entities', value: archive.stats.entities },
                    { label: 'Dated events', value: archive.stats.timelineEvents },
                    { label: 'Duplicates', value: archive.stats.duplicates },
                    {
                      label: 'Languages',
                      value: Object.keys(archive.stats.languages).length,
                    },
                  ].map((stat) => (
                    <div
                      key={stat.label}
                      className="rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2"
                    >
                      <dt className="text-xs text-ink-500">{stat.label}</dt>
                      <dd className="font-display text-xl text-teal-800">{stat.value}</dd>
                    </div>
                  ))}
                </dl>
              </section>

              <section aria-labelledby="archive-upload-heading" className="rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm">
                <h2 id="archive-upload-heading" className="font-display text-lg text-teal-800">
                  Add material
                </h2>
                <div className="mt-3">
                  <ArchiveUploadPanel
                    archiveId={archive.id}
                    archiveName={archive.name}
                    onUploaded={handleUploaded}
                  />
                </div>
              </section>

              <section aria-labelledby="archive-files-heading" className="rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm">
                <h2 id="archive-files-heading" className="font-display text-lg text-teal-800">
                  Files
                </h2>
                <p className="mt-1 text-xs text-ink-500">
                  Status is the real pipeline state. A file with no extractable text is reported
                  as failed or partial, never as completed.
                </p>
                <div className="mt-3">
                  <ArchiveFileTable
                    files={archive.files}
                    onOpen={openFile}
                    onReprocess={handleReprocess}
                    onDelete={handleDeleteFile}
                    actionBusyId={actionBusyId}
                  />
                </div>
              </section>

              <section aria-labelledby="archive-timeline-heading" className="rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm">
                <h2 id="archive-timeline-heading" className="font-display text-lg text-teal-800">
                  Historical timeline
                </h2>
                <p className="mt-1 text-sm text-ink-500">
                  Dated passages from this archive, in chronological order. BCE dates sort before
                  CE; approximate and century dates keep their precision.
                </p>
                <div className="mt-4">
                  {timeline ? (
                    <ArchiveTimeline
                      events={timeline.events}
                      limitations={timeline.limitations}
                      onOpenFile={openFile}
                    />
                  ) : (
                    <p className="text-sm text-ink-500">The timeline could not be loaded.</p>
                  )}
                </div>
              </section>
            </>
          ) : null}
        </div>
      </div>

      <div id="archive-file-detail" className="scroll-mt-4">
        {filePhase === 'loading' ? (
          <p role="status" className="rounded-xl border border-ivory-300 bg-ivory p-5 text-sm text-ink-700">
            Loading the extracted text…
          </p>
        ) : null}
        {filePhase === 'error' ? (
          <p role="alert" className="rounded-xl border border-terracotta-300 bg-terracotta-50 p-5 text-sm text-terracotta-800">
            {fileError}
          </p>
        ) : null}
        {fileDetail ? (
          <ArchiveFileDetailPanel
            detail={fileDetail}
            onClose={() => setFileDetail(null)}
            onOpenFile={openFile}
            onReprocess={handleReprocess}
            actionBusyId={actionBusyId}
          />
        ) : null}
      </div>
    </div>
  );
}

export default ArchivePage;
