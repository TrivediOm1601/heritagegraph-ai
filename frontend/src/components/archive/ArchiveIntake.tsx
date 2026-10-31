import { useRef, useState } from 'react';
import type { ChangeEvent, FormEvent } from 'react';

import {
  ARCHIVE_ACCEPT,
  MAX_FILES_PER_UPLOAD,
  formatBytes,
  uploadArchiveFiles,
} from '../../services/archiveApi';
import type { ArchiveUploadResult } from '../../types/archive';
import type { CreateArchiveInput } from '../../services/archiveApi';
import { ApiError } from '../../services/api';

interface CreateArchiveFormProps {
  onCreate: (input: CreateArchiveInput) => Promise<void>;
}

/** Create an archive: name is required, everything else is optional. */
export function CreateArchiveForm({ onCreate }: CreateArchiveFormProps) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [region, setRegion] = useState('');
  const [tags, setTags] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setError('Give the archive a name.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onCreate({
        name: trimmed,
        description: description.trim() || undefined,
        region: region.trim() || undefined,
        tags: tags
          .split(',')
          .map((tag) => tag.trim())
          .filter(Boolean),
      });
      setName('');
      setDescription('');
      setRegion('');
      setTags('');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the archive.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-3" aria-label="Create an archive">
      <div>
        <label htmlFor="archive-name" className="block text-sm font-medium text-ink-700">
          Archive name
        </label>
        <input
          id="archive-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={200}
          placeholder="Gujarat Stepwell Heritage Collection"
          className="mt-1 w-full rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2 text-sm text-ink-900 placeholder:text-ink-500 focus:border-teal-500 focus:outline-none"
        />
      </div>
      <div>
        <label htmlFor="archive-description" className="block text-sm font-medium text-ink-700">
          Description <span className="font-normal text-ink-500">(optional)</span>
        </label>
        <textarea
          id="archive-description"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          rows={3}
          maxLength={4000}
          placeholder="Photographs, scanned reports and field notes about stepwells and regional architecture."
          className="mt-1 w-full rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2 text-sm text-ink-900 placeholder:text-ink-500 focus:border-teal-500 focus:outline-none"
        />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="archive-region" className="block text-sm font-medium text-ink-700">
            Region
          </label>
          <input
            id="archive-region"
            value={region}
            onChange={(event) => setRegion(event.target.value)}
            maxLength={120}
            placeholder="Gujarat"
            className="mt-1 w-full rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2 text-sm text-ink-900 placeholder:text-ink-500 focus:border-teal-500 focus:outline-none"
          />
        </div>
        <div>
          <label htmlFor="archive-tags" className="block text-sm font-medium text-ink-700">
            Tags
          </label>
          <input
            id="archive-tags"
            value={tags}
            onChange={(event) => setTags(event.target.value)}
            maxLength={200}
            placeholder="stepwell, architecture"
            className="mt-1 w-full rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2 text-sm text-ink-900 placeholder:text-ink-500 focus:border-teal-500 focus:outline-none"
          />
        </div>
      </div>
      {error ? (
        <p role="alert" className="text-sm text-terracotta-700">
          {error}
        </p>
      ) : null}
      <button
        type="submit"
        disabled={busy}
        className="self-start rounded-lg bg-teal-600 px-4 py-2 text-sm font-medium text-ivory transition-colors hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {busy ? 'Creating…' : 'Create archive'}
      </button>
    </form>
  );
}

interface ArchiveUploadPanelProps {
  archiveId: string | null;
  archiveName?: string;
  onUploaded: (result: ArchiveUploadResult) => void;
}

/** Multi-file upload with per-file rejection reporting. */
export function ArchiveUploadPanel({
  archiveId,
  archiveName,
  onUploaded,
}: ArchiveUploadPanelProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ArchiveUploadResult | null>(null);

  const onChange = (event: ChangeEvent<HTMLInputElement>) => {
    setSelected(Array.from(event.target.files ?? []));
    setResult(null);
    setError(null);
  };

  const upload = async () => {
    if (!archiveId) return;
    if (selected.length === 0) {
      setError('Choose at least one file.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const uploaded = await uploadArchiveFiles(archiveId, selected);
      setResult(uploaded);
      setSelected([]);
      if (inputRef.current) inputRef.current.value = '';
      onUploaded(uploaded);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The upload failed. Please try again.');
    } finally {
      setBusy(false);
    }
  };

  if (!archiveId) {
    return (
      <p className="rounded-lg border border-dashed border-ivory-300 bg-ivory-50 px-3 py-3 text-sm text-ink-500">
        Create or select an archive to upload material.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div>
        <label htmlFor="archive-files" className="block text-sm font-medium text-ink-700">
          Add material to {archiveName ?? 'this archive'}
        </label>
        <input
          id="archive-files"
          ref={inputRef}
          type="file"
          multiple
          accept={ARCHIVE_ACCEPT}
          onChange={onChange}
          className="mt-1 block w-full cursor-pointer rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2 text-sm text-ink-700 file:mr-3 file:rounded-md file:border-0 file:bg-teal-600 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-ivory hover:file:bg-teal-700"
        />
        <p className="mt-1 text-xs text-ink-500">
          Images, PDF, DOCX, PPTX, TXT, Markdown, or a ZIP of those. Up to{' '}
          {MAX_FILES_PER_UPLOAD} files per upload; each file up to 25 MB (ZIP up to 100 MB).
        </p>
      </div>

      {selected.length > 0 ? (
        <ul className="flex flex-col gap-1 rounded-lg border border-ivory-300 bg-ivory-50 p-2 text-sm text-ink-700">
          {selected.map((file) => (
            <li key={`${file.name}-${file.size}`} className="flex justify-between gap-3">
              <span className="truncate">{file.name}</span>
              <span className="shrink-0 text-xs text-ink-500">{formatBytes(file.size)}</span>
            </li>
          ))}
        </ul>
      ) : null}

      <button
        type="button"
        onClick={upload}
        disabled={busy || selected.length === 0}
        className="self-start rounded-lg bg-teal-600 px-4 py-2 text-sm font-medium text-ivory transition-colors hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {busy ? 'Uploading…' : `Upload ${selected.length || ''} file${selected.length === 1 ? '' : 's'}`.trim()}
      </button>

      {error ? (
        <p role="alert" className="rounded-lg border border-terracotta-300 bg-terracotta-50 px-3 py-2 text-sm text-terracotta-700">
          {error}
        </p>
      ) : null}

      {result ? (
        <div role="status" className="rounded-lg border border-teal-200 bg-teal-50 px-3 py-2 text-sm text-teal-900">
          <p className="font-medium">{result.message}</p>
          {result.rejected.length > 0 ? (
            <div className="mt-2">
              <p className="font-medium text-terracotta-800">
                Skipped ({result.rejected.length})
              </p>
              <ul className="mt-1 list-inside list-disc text-xs text-terracotta-800">
                {result.rejected.map((item) => (
                  <li key={`${item.filename}-${item.reason}`}>
                    <span className="font-medium">{item.filename}</span> — {item.reason}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {result.warnings.length > 0 ? (
            <ul className="mt-2 list-inside list-disc text-xs text-ink-700">
              {result.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
