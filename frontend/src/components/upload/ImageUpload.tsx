import { useEffect, useId, useRef, useState } from 'react';
import type { ChangeEvent, FormEvent } from 'react';

import { useAnalysis } from '../../hooks/useAnalysis';
import { Photo3DViewer } from '../viewer/Photo3DViewer';
import {
  formatBytes,
  validateImageFile,
  type SubmitAnalysisSuccess,
} from '../../services/api';

interface ImageUploadProps {
  /**
   * Called once per accepted submission with the server result. Lets later
   * parts (e.g. the Analysis page) take over once the upload is queued.
   */
  onSubmitted?: (result: SubmitAnalysisSuccess) => void;
}

const ACCEPT_ATTR = [
  '.jpg',
  '.jpeg',
  '.png',
  '.webp',
  '.gif',
  '.tif',
  '.tiff',
  '.avif',
  'image/jpeg',
  'image/png',
  'image/webp',
  'image/gif',
  'image/tiff',
  'image/avif',
].join(',');

/**
 * A2 — image upload with local preview and submission lifecycle.
 *
 * The browser rejects obviously invalid files (unsupported type, empty or
 * oversized) via `validateImageFile` before any request is made. The
 * submission itself is delegated to the `useAnalysis` hook, which guards
 * against duplicate submissions and never reports success on a backend
 * error. States: idle → (file selected with preview) → submitting →
 * success / error.
 */
export function ImageUpload({ onSubmitted }: ImageUploadProps) {
  const { phase, error, submission, isSubmitting, submitAnalysis, reset } = useAnalysis();
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();

  // Revoke the object URL backing the preview when it is replaced,
  // cleared, or the component unmounts — otherwise every selection leaks
  // a blob URL for the lifetime of the page.
  useEffect(() => {
    if (!previewUrl) return;
    return () => URL.revokeObjectURL(previewUrl);
  }, [previewUrl]);

  const clearSelection = () => {
    setFile(null);
    setPreviewUrl(null); // the effect below revokes the old object URL
    setFileError(null);
    reset();
  };

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const chosen = event.target.files?.[0] ?? null;
    // Reset so choosing the same file again re-triggers `change`.
    event.target.value = '';

    if (!chosen) {
      clearSelection();
      return;
    }

    const problem = validateImageFile(chosen);
    if (problem) {
      clearSelection();
      setFileError(problem); // rejected in the browser; nothing is submitted
      return;
    }

    setFileError(null);
    reset(); // a new selection clears any previous success/error state
    setFile(chosen);
    setPreviewUrl(URL.createObjectURL(chosen));
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    // The hook also guards in-flight submissions; this avoids a second
    // state read race in the UI layer.
    if (!file || isSubmitting) return;

    const result = await submitAnalysis(file);
    if (result && onSubmitted) onSubmitted(result);
  };

  return (
    <section
      aria-labelledby="upload-heading"
      className="rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm sm:p-6"
    >
      <h2 id="upload-heading" className="font-display text-lg text-teal-800">
        Upload an image for analysis
      </h2>
      <p className="mt-1 text-sm text-ink-500">
        JPEG, PNG, WebP, GIF, TIFF or AVIF — up to 10 MB.
      </p>

      <form onSubmit={handleSubmit} className="mt-4 flex flex-col gap-4 sm:flex-row">
        {/* Local preview of the chosen file */}
        {previewUrl && file ? (
          <figure className="flex items-center gap-3 rounded-lg border border-ivory-300 bg-ivory-50 p-3">
            <img
              src={previewUrl}
              alt={`Preview of ${file.name}`}
              className="h-24 w-24 rounded-md border border-ivory-300 object-cover"
            />
            <figcaption className="max-w-48 text-sm text-ink-700">
              <span className="block break-all font-medium">{file.name}</span>
              <span className="block text-xs text-ink-500">{formatBytes(file.size)}</span>
            </figcaption>
          </figure>
        ) : null}

        <div className="flex flex-1 flex-col justify-center gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <input
              ref={inputRef}
              id={inputId}
              type="file"
              accept={ACCEPT_ATTR}
              className="sr-only"
              onChange={handleFileChange}
              disabled={isSubmitting}
            />
            <label
              htmlFor={inputId}
              className="cursor-pointer rounded-lg border border-teal-300 bg-ivory-50 px-3 py-2 text-sm font-medium text-teal-700 transition-colors hover:bg-teal-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {file ? 'Choose a different image' : 'Choose image'}
            </label>

            <button
              type="submit"
              disabled={!file || isSubmitting}
              className="rounded-lg bg-teal-600 px-3 py-2 text-sm font-medium text-ivory transition-colors hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isSubmitting ? 'Uploading…' : 'Submit for analysis'}
            </button>
          </div>

          {/* Browser-side rejection (invalid or oversized file) */}
          {fileError ? (
            <p role="alert" className="text-sm text-terracotta-700">
              {fileError}
            </p>
          ) : null}

          {/* Submission lifecycle feedback */}
          {phase === 'submitting' ? (
            <p role="status" aria-live="polite" className="text-sm text-ink-700">
              Uploading {file?.name} — this should only take a moment…
            </p>
          ) : null}

          {phase === 'success' && submission ? (
            <p
              role="status"
              aria-live="polite"
              className="rounded-lg border border-teal-300 bg-teal-50 px-3 py-2 text-sm text-teal-800"
            >
              Upload accepted — analysis {submission.analysisId} is {submission.status}.
            </p>
          ) : null}

          {phase === 'error' && error ? (
            <div
              role="alert"
              className="rounded-lg border border-terracotta-300 bg-terracotta-50 px-3 py-2 text-sm text-terracotta-700"
            >
              <span className="font-medium">Upload failed.</span> {error}
            </div>
          ) : null}
        </div>
      </form>

      {previewUrl && file ? (
        <div className="mt-3">
          <Photo3DViewer src={previewUrl} />
        </div>
      ) : null}
    </section>
  );
}

export default ImageUpload;
