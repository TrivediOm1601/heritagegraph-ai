import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';

import {
  addCollectionItem,
  addCollectionNote,
  collectionReportUrl,
  createCollection,
  deleteCollectionNote,
  getCollection,
  listArchives,
  listCollections,
  removeCollectionItem,
} from '../services/archiveApi';
import { ApiError, downloadPdf } from '../services/accountApi';
import type {
  Archive,
  ResearchCollection,
  ResearchCollectionDetail,
} from '../types/archive';

function reportFilename(name: string): string {
  const slug = (name || 'collection')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60) || 'collection';
  return `heritagegraph-collection-${slug}-${new Date().toISOString().slice(0, 10)}.pdf`;
}

/**
 * Personal Research Workspace.
 *
 * A collection groups archives with your own notes and exports one merged
 * report. Ownership is enforced by the API; this page only ever sees the
 * signed-in user's material.
 */
export function ResearchPage() {
  const [collections, setCollections] = useState<ResearchCollection[]>([]);
  const [archives, setArchives] = useState<Archive[]>([]);
  const [listPhase, setListPhase] = useState<'loading' | 'error' | 'ready'>('loading');
  const [listError, setListError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ResearchCollectionDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [newName, setNewName] = useState('');
  const [newDescription, setNewDescription] = useState('');
  const [archiveToAdd, setArchiveToAdd] = useState('');
  const [noteBody, setNoteBody] = useState('');
  const [noteSubject, setNoteSubject] = useState('');

  const refreshCollections = async () => {
    try {
      const [items, archiveItems] = await Promise.all([listCollections(), listArchives()]);
      setCollections(items);
      setArchives(archiveItems);
      setListPhase('ready');
      setListError(null);
    } catch (err) {
      setListPhase('error');
      setListError(err instanceof ApiError ? err.message : 'Could not load your workspace.');
    }
  };

  useEffect(() => {
    void refreshCollections();
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    getCollection(selectedId).then(
      (data) => {
        if (!cancelled) {
          setDetail(data);
          setDetailLoading(false);
        }
      },
      (err: unknown) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : 'Could not load the collection.');
          setDetailLoading(false);
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const reloadDetail = async (collectionId: string) => {
    const data = await getCollection(collectionId);
    setDetail(data);
    await refreshCollections();
  };

  const handleCreate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!newName.trim()) {
      setError('Give the collection a name.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await createCollection({
        name: newName.trim(),
        description: newDescription.trim() || undefined,
      });
      setNewName('');
      setNewDescription('');
      setNotice(`Collection “${created.name}” created.`);
      await refreshCollections();
      setSelectedId(created.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the collection.');
    } finally {
      setBusy(false);
    }
  };

  const handleAddArchive = async () => {
    if (!detail || !archiveToAdd) return;
    setBusy(true);
    setError(null);
    try {
      await addCollectionItem(detail.id, { type: 'archive', id: archiveToAdd });
      setArchiveToAdd('');
      setNotice('Archive added to the collection.');
      await reloadDetail(detail.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not add that archive.');
    } finally {
      setBusy(false);
    }
  };

  const handleRemoveItem = async (itemId: string) => {
    if (!detail) return;
    setBusy(true);
    setError(null);
    try {
      await removeCollectionItem(detail.id, { type: 'archive', id: itemId });
      await reloadDetail(detail.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove that item.');
    } finally {
      setBusy(false);
    }
  };

  const handleAddNote = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!detail) return;
    if (!noteBody.trim()) {
      setError('Write something before saving the note.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await addCollectionNote(detail.id, {
        body: noteBody.trim(),
        subjectQuery: noteSubject.trim() || undefined,
      });
      setNoteBody('');
      setNoteSubject('');
      setNotice('Note saved.');
      await reloadDetail(detail.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the note.');
    } finally {
      setBusy(false);
    }
  };

  const handleDeleteNote = async (noteId: string) => {
    if (!detail) return;
    setBusy(true);
    setError(null);
    try {
      await deleteCollectionNote(noteId);
      await reloadDetail(detail.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not delete the note.');
    } finally {
      setBusy(false);
    }
  };

  const handleReport = async () => {
    if (!detail) return;
    setError(null);
    try {
      await downloadPdf(collectionReportUrl(detail.id), reportFilename(detail.name));
      setNotice('Collection report downloaded.');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The report could not be generated.');
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <section className="rounded-2xl border border-ivory-300 bg-ivory-100 p-6 shadow-sm sm:p-8">
        <p className="text-xs font-semibold uppercase tracking-[0.25em] text-terracotta-600">
          Research Workspace
        </p>
        <h1 className="mt-2 max-w-3xl font-display text-3xl leading-tight text-teal-800 sm:text-4xl">
          Keep the sources, notes and report together
        </h1>
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-ink-700 sm:text-base">
          Group archives into a collection, record what you are investigating, and export a
          single report that keeps every passage tied to the file it came from.
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

      <div className="grid gap-6 lg:grid-cols-[20rem_minmax(0,1fr)]">
        <aside className="flex flex-col gap-4">
          <section className="rounded-xl border border-ivory-300 bg-ivory p-4 shadow-sm">
            <h2 className="font-display text-lg text-teal-800">Collections</h2>
            {listPhase === 'loading' ? (
              <p role="status" className="mt-2 text-sm text-ink-500">
                Loading collections…
              </p>
            ) : null}
            {listPhase === 'error' ? (
              <p role="alert" className="mt-2 text-sm text-terracotta-700">
                {listError}
              </p>
            ) : null}
            {listPhase === 'ready' && collections.length === 0 ? (
              <p className="mt-2 text-sm text-ink-500">No collections yet.</p>
            ) : null}
            <ul className="mt-2 flex flex-col gap-1">
              {collections.map((collection) => (
                <li key={collection.id}>
                  <button
                    type="button"
                    onClick={() => {
                      setSelectedId(collection.id);
                      setError(null);
                    }}
                    aria-current={selectedId === collection.id ? 'true' : undefined}
                    className={
                      selectedId === collection.id
                        ? 'w-full rounded-lg bg-teal-600 px-3 py-2 text-left text-ivory'
                        : 'w-full rounded-lg px-3 py-2 text-left transition-colors hover:bg-ivory-100'
                    }
                  >
                    <span className="block truncate text-sm font-medium">{collection.name}</span>
                    <span
                      className={
                        selectedId === collection.id
                          ? 'mt-0.5 block text-xs text-teal-100'
                          : 'mt-0.5 block text-xs text-ink-500'
                      }
                    >
                      {collection.itemCount} archive(s) · {collection.noteCount} note(s)
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>

          <section className="rounded-xl border border-ivory-300 bg-ivory p-4 shadow-sm">
            <h2 className="font-display text-lg text-teal-800">New collection</h2>
            <form onSubmit={handleCreate} className="mt-2 flex flex-col gap-2">
              <label className="text-xs font-medium text-ink-700">
                Name
                <input
                  value={newName}
                  onChange={(event) => setNewName(event.target.value)}
                  maxLength={200}
                  placeholder="Gujarat Stepwell Research"
                  className="mt-1 w-full rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2 text-sm font-normal text-ink-900 placeholder:text-ink-500 focus:border-teal-500 focus:outline-none"
                />
              </label>
              <label className="text-xs font-medium text-ink-700">
                Description <span className="font-normal text-ink-500">(optional)</span>
                <textarea
                  value={newDescription}
                  onChange={(event) => setNewDescription(event.target.value)}
                  rows={2}
                  maxLength={4000}
                  className="mt-1 w-full rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2 text-sm font-normal text-ink-900 focus:border-teal-500 focus:outline-none"
                />
              </label>
              <button
                type="submit"
                disabled={busy}
                className="self-start rounded-lg bg-teal-600 px-3 py-2 text-sm font-medium text-ivory hover:bg-teal-700 disabled:opacity-50"
              >
                {busy ? 'Saving…' : 'Create collection'}
              </button>
            </form>
          </section>
        </aside>

        <div className="flex min-w-0 flex-col gap-5">
          {!selectedId ? (
            <section className="rounded-xl border border-dashed border-ivory-300 bg-ivory p-8 text-center">
              <p className="font-display text-xl text-teal-800">Select or create a collection</p>
              <p className="mx-auto mt-2 max-w-md text-sm text-ink-500">
                A collection can hold your archives and the notes you write while working
                through them.
              </p>
            </section>
          ) : detailLoading && !detail ? (
            <p role="status" className="rounded-xl border border-ivory-300 bg-ivory p-5 text-sm text-ink-700">
              Loading the collection…
            </p>
          ) : detail ? (
            <>
              <section className="rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h2 className="font-display text-2xl text-teal-800">{detail.name}</h2>
                    {detail.description ? (
                      <p className="mt-1 text-sm text-ink-500">{detail.description}</p>
                    ) : null}
                  </div>
                  <button
                    type="button"
                    onClick={handleReport}
                    className="rounded-lg bg-teal-600 px-3 py-2 text-sm font-medium text-ivory hover:bg-teal-700"
                  >
                    Download collection report
                  </button>
                </div>

                <h3 className="mt-4 font-display text-base text-teal-800">Archives</h3>
                {detail.items.length === 0 ? (
                  <p className="mt-1 text-sm text-ink-500">No archive has been added yet.</p>
                ) : (
                  <ul className="mt-2 flex flex-col gap-1">
                    {detail.items.map((item) => (
                      <li
                        key={`${item.type}-${item.id}`}
                        className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2"
                      >
                        <span className="text-sm text-ink-900">{item.name}</span>
                        <button
                          type="button"
                          onClick={() => handleRemoveItem(item.id)}
                          disabled={busy}
                          className="text-xs font-medium text-terracotta-700 hover:underline disabled:opacity-50"
                        >
                          Remove
                        </button>
                      </li>
                    ))}
                  </ul>
                )}

                <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-end">
                  <label className="flex-1 text-xs font-medium text-ink-700">
                    Add an archive
                    <select
                      value={archiveToAdd}
                      onChange={(event) => setArchiveToAdd(event.target.value)}
                      className="mt-1 w-full rounded-lg border border-ivory-300 bg-ivory-50 px-2 py-2 text-sm font-normal text-ink-900 focus:border-teal-500 focus:outline-none"
                    >
                      <option value="">Choose one of your archives…</option>
                      {archives
                        .filter(
                          (archive) => !detail.items.some((item) => item.id === archive.id),
                        )
                        .map((archive) => (
                          <option key={archive.id} value={archive.id}>
                            {archive.name} ({archive.stats.files} files)
                          </option>
                        ))}
                    </select>
                  </label>
                  <button
                    type="button"
                    onClick={handleAddArchive}
                    disabled={busy || !archiveToAdd}
                    className="rounded-lg border border-teal-600 px-3 py-2 text-sm font-medium text-teal-700 hover:bg-teal-50 disabled:opacity-50"
                  >
                    Add archive
                  </button>
                </div>
              </section>

              <section className="rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm">
                <h3 className="font-display text-lg text-teal-800">Research notes</h3>
                {detail.notes.length === 0 ? (
                  <p className="mt-1 text-sm text-ink-500">
                    No notes yet. Record what you are looking for, or a comparison between two
                    sources.
                  </p>
                ) : (
                  <ul className="mt-2 flex flex-col gap-2">
                    {detail.notes.map((note) => (
                      <li
                        key={note.id}
                        className="rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="text-xs font-medium uppercase tracking-wide text-terracotta-700">
                            {note.subjectQuery || 'General note'}
                          </span>
                          <button
                            type="button"
                            onClick={() => handleDeleteNote(note.id)}
                            disabled={busy}
                            className="text-xs text-ink-500 hover:text-terracotta-700 disabled:opacity-50"
                          >
                            Delete
                          </button>
                        </div>
                        <p className="mt-1 whitespace-pre-wrap text-sm text-ink-900">{note.body}</p>
                      </li>
                    ))}
                  </ul>
                )}

                <form onSubmit={handleAddNote} className="mt-4 flex flex-col gap-2">
                  <label className="text-xs font-medium text-ink-700">
                    Subject <span className="font-normal text-ink-500">(optional)</span>
                    <input
                      value={noteSubject}
                      onChange={(event) => setNoteSubject(event.target.value)}
                      maxLength={512}
                      placeholder="Rani ki Vav"
                      className="mt-1 w-full rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2 text-sm font-normal text-ink-900 placeholder:text-ink-500 focus:border-teal-500 focus:outline-none"
                    />
                  </label>
                  <label className="text-xs font-medium text-ink-700">
                    Note
                    <textarea
                      value={noteBody}
                      onChange={(event) => setNoteBody(event.target.value)}
                      rows={3}
                      maxLength={20000}
                      className="mt-1 w-full rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2 text-sm font-normal text-ink-900 focus:border-teal-500 focus:outline-none"
                    />
                  </label>
                  <button
                    type="submit"
                    disabled={busy}
                    className="self-start rounded-lg bg-teal-600 px-3 py-2 text-sm font-medium text-ivory hover:bg-teal-700 disabled:opacity-50"
                  >
                    {busy ? 'Saving…' : 'Save note'}
                  </button>
                </form>
              </section>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default ResearchPage;
