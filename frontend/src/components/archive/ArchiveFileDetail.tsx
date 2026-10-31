import { archiveFileDownloadUrl } from '../../services/archiveApi';
import type { ArchiveFileDetail } from '../../types/archive';
import { formatWhen, KindBadge, StatusBadge } from './ArchiveStatus';

interface ArchiveFileDetailProps {
  detail: ArchiveFileDetail;
  onClose: () => void;
  onOpenFile: (fileId: string) => void;
  onReprocess: (fileId: string) => void;
  actionBusyId?: string | null;
}

const TEXT_SOURCE_LABELS: Record<string, string> = {
  'text-layer': 'PDF text layer',
  ocr: 'OCR',
  extracted: 'Extracted from the document',
  none: 'No machine-readable text',
};

const PRECISION_LABELS: Record<string, string> = {
  exact: 'Exact date in source',
  approximate: 'Approximate date',
  century: 'Century',
  range: 'Date range',
  unknown: 'Uncertain date',
};

function timelineLabel(event: ArchiveFileDetail['timeline'][number]): string {
  if (event.yearStart == null) return event.dateText;
  if (event.yearEnd != null && event.yearEnd !== event.yearStart) {
    return `${event.dateText} (${event.yearStart} to ${event.yearEnd})`;
  }
  return `${event.dateText} (${event.yearStart})`;
}

/** One file's digitised content, provenance and honest limitations. */
export function ArchiveFileDetailPanel({
  detail,
  onClose,
  onOpenFile,
  onReprocess,
  actionBusyId = null,
}: ArchiveFileDetailProps) {
  const { file } = detail;

  return (
    <section
      aria-label={`Details for ${file.filename}`}
      className="rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="break-words font-display text-xl text-teal-800">{file.filename}</h2>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <KindBadge kind={file.kind} />
            <StatusBadge status={file.status} />
            {file.language ? (
              <span className="text-xs text-ink-500">
                {file.language}
                {file.languageScript ? ` · ${file.languageScript} script` : ''}
              </span>
            ) : null}
            <span className="text-xs text-ink-500">{formatWhen(file.updatedAt)}</span>
          </div>
          {detail.parentFile ? (
            <p className="mt-1 text-xs text-ink-500">
              Extracted from{' '}
              <button
                type="button"
                onClick={() => onOpenFile(detail.parentFile!.id)}
                className="font-medium text-teal-700 underline-offset-2 hover:underline"
              >
                {detail.parentFile.filename}
              </button>
              {file.archivePath ? ` · ${file.archivePath}` : ''}
            </p>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          <a
            href={archiveFileDownloadUrl(file.id)}
            className="rounded-lg border border-ivory-300 px-3 py-1.5 text-sm font-medium text-ink-700 hover:bg-ivory-100"
          >
            Download original
          </a>
          {file.kind !== 'zip' && (file.status === 'failed' || file.status === 'partial') ? (
            <button
              type="button"
              onClick={() => onReprocess(file.id)}
              disabled={actionBusyId === file.id}
              className="rounded-lg border border-teal-600 px-3 py-1.5 text-sm font-medium text-teal-700 hover:bg-teal-50 disabled:opacity-50"
            >
              {actionBusyId === file.id ? 'Queueing…' : 'Reprocess'}
            </button>
          ) : null}
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg px-2 py-1 text-sm text-ink-500 hover:bg-ivory-100"
          >
            Close
          </button>
        </div>
      </div>

      {file.error ? (
        <p role="alert" className="mt-3 rounded-lg border border-terracotta-300 bg-terracotta-50 px-3 py-2 text-sm text-terracotta-800">
          {file.error}
        </p>
      ) : null}

      {file.warnings.length > 0 ? (
        <ul className="mt-3 list-inside list-disc rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2 text-xs text-ink-700">
          {file.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}

      {detail.duplicateOf ? (
        <div className="mt-3 rounded-lg border border-terracotta-200 bg-terracotta-50 px-3 py-2 text-sm text-terracotta-800">
          <p className="font-medium">
            Flagged as a possible {file.duplicateKind?.replace('_', ' ')} duplicate
          </p>
          <p className="mt-1 text-xs">
            It is matched against{' '}
            <button
              type="button"
              onClick={() => onOpenFile(detail.duplicateOf!.id)}
              className="font-medium underline underline-offset-2"
            >
              {detail.duplicateOf.filename}
            </button>
            . Nothing was deleted or merged; review both files.
          </p>
        </div>
      ) : null}

      <div className="mt-4 grid gap-5 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <div>
          <h3 className="font-display text-base text-teal-800">
            Extracted text
            <span className="ml-2 text-xs font-normal text-ink-500">
              {detail.pages.length} {file.pageLabel.toLowerCase()}(s)
            </span>
          </h3>
          {detail.pages.length === 0 ? (
            <p className="mt-2 text-sm text-ink-500">No pages have been extracted yet.</p>
          ) : (
            <div className="mt-2 flex flex-col gap-2">
              {detail.pages.map((page) => (
                <details
                  key={page.pageNumber}
                  open={detail.pages.length === 1}
                  className="rounded-lg border border-ivory-300 bg-ivory-50"
                >
                  <summary className="flex cursor-pointer flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm font-medium text-ink-700">
                    <span>
                      {file.pageLabel} {page.pageNumber}
                    </span>
                    <span className="text-xs font-normal text-ink-500">
                      {page.charCount} characters · {TEXT_SOURCE_LABELS[page.textSource] ?? page.textSource}
                    </span>
                  </summary>
                  <div className="border-t border-ivory-200 px-3 py-3">
                    {page.text ? (
                      <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-ink-900">
                        {page.text}
                      </pre>
                    ) : (
                      <p className="text-sm text-ink-500">
                        No machine-readable text on this {file.pageLabel.toLowerCase()}.
                        {page.textSource === 'none'
                          ? ' OCR found nothing here; the original is preserved.'
                          : ''}
                      </p>
                    )}
                  </div>
                </details>
              ))}
            </div>
          )}

          <h3 className="mt-5 font-display text-base text-teal-800">
            Dated passages
            <span className="ml-2 text-xs font-normal text-ink-500">
              {detail.timeline.length} event(s)
            </span>
          </h3>
          {detail.timeline.length === 0 ? (
            <p className="mt-2 text-sm text-ink-500">
              No dated passage was found in the extracted text.
            </p>
          ) : (
            <ol className="mt-2 flex flex-col gap-2 border-l-2 border-ivory-300 pl-4">
              {detail.timeline.map((event) => (
                <li key={event.id} className="relative">
                  <span
                    aria-hidden="true"
                    className="absolute -left-[1.32rem] top-1.5 h-2.5 w-2.5 rounded-full bg-terracotta-500"
                  />
                  <p className="text-sm font-medium text-ink-900">{timelineLabel(event)}</p>
                  <p className="text-sm text-ink-700">{event.title}</p>
                  <p className="text-xs text-ink-500">
                    {PRECISION_LABELS[event.precision] ?? event.precision}
                    {event.sourcePage ? ` · ${file.pageLabel} ${event.sourcePage}` : ''} ·{' '}
                    {event.verificationStatus}
                  </p>
                </li>
              ))}
            </ol>
          )}
        </div>

        <div>
          <h3 className="font-display text-base text-teal-800">
            Entity candidates
            <span className="ml-2 text-xs font-normal text-ink-500">{detail.entities.length}</span>
          </h3>
          <p className="mt-1 text-xs text-ink-500">
            Extraction output, not a verified identification. Each candidate keeps the passage it
            came from.
          </p>
          {detail.entities.length === 0 ? (
            <p className="mt-2 text-sm text-ink-500">No entity candidate was extracted.</p>
          ) : (
            <ul className="mt-2 flex max-h-96 flex-col gap-2 overflow-auto pr-1">
              {detail.entities.map((entity) => (
                <li
                  key={`${entity.id}-${entity.pageNumber ?? 'x'}`}
                  className="rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium text-ink-900">{entity.name}</span>
                    <span className="rounded-full bg-ivory-200 px-2 py-0.5 text-xs text-ink-700">
                      {entity.type}
                    </span>
                  </div>
                  <p className="mt-0.5 text-xs text-ink-500">
                    {entity.extractionMethod}
                    {entity.pageNumber ? ` · ${file.pageLabel} ${entity.pageNumber}` : ''}
                  </p>
                  {entity.excerpt ? (
                    <p className="mt-1 border-l-2 border-ivory-300 pl-2 text-xs italic text-ink-700">
                      “{entity.excerpt}”
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          )}

          <h3 className="mt-5 font-display text-base text-teal-800">Limitations</h3>
          <ul className="mt-2 list-inside list-disc text-xs text-ink-500">
            {detail.limitations.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}

export default ArchiveFileDetailPanel;
