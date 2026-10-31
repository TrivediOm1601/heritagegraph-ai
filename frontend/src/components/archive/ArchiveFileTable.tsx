import { archiveFileDownloadUrl } from '../../services/archiveApi';
import type { ArchiveFileItem } from '../../types/archive';
import { formatWhen, KindBadge, StatusBadge, isBusy } from './ArchiveStatus';

interface ArchiveFileTableProps {
  files: ArchiveFileItem[];
  onOpen: (fileId: string) => void;
  onReprocess: (fileId: string) => void;
  onDelete: (fileId: string) => void;
  actionBusyId?: string | null;
}

/** Inventory of one archive: real status, page count, language and actions. */
export function ArchiveFileTable({
  files,
  onOpen,
  onReprocess,
  onDelete,
  actionBusyId = null,
}: ArchiveFileTableProps) {
  if (files.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-ivory-300 bg-ivory-50 px-4 py-8 text-center">
        <p className="font-display text-teal-800">Nothing archived yet</p>
        <p className="mx-auto mt-1 max-w-md text-sm text-ink-500">
          Upload photographs, a scanned report, a PDF or a ZIP of field notes. Every file is
          digitised independently and its extracted text becomes searchable.
        </p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[44rem] border-collapse text-left text-sm">
        <caption className="sr-only">Files in this archive</caption>
        <thead>
          <tr className="border-b border-ivory-300 text-xs uppercase tracking-wide text-ink-500">
            <th scope="col" className="py-2 pr-3 font-medium">File</th>
            <th scope="col" className="py-2 pr-3 font-medium">Status</th>
            <th scope="col" className="py-2 pr-3 font-medium">Pages</th>
            <th scope="col" className="py-2 pr-3 font-medium">Language</th>
            <th scope="col" className="py-2 font-medium">Actions</th>
          </tr>
        </thead>
        <tbody>
          {files.map((file) => (
            <tr key={file.id} className="border-b border-ivory-200 align-top">
              <td className="py-3 pr-3">
                <button
                  type="button"
                  onClick={() => onOpen(file.id)}
                  className="text-left font-medium text-teal-800 underline-offset-2 hover:underline"
                >
                  {file.filename}
                </button>
                <div className="mt-1 flex flex-wrap items-center gap-1.5">
                  <KindBadge kind={file.kind} />
                  {file.archivePath && file.archivePath !== file.filename ? (
                    <span className="text-xs text-ink-500" title={file.archivePath}>
                      from ZIP
                    </span>
                  ) : null}
                  {file.duplicateKind ? (
                    <span
                      className="rounded-full bg-terracotta-100 px-2 py-0.5 text-xs font-medium text-terracotta-800"
                      title={`Possible ${file.duplicateKind.replace('_', ' ')} duplicate — kept, not deleted`}
                    >
                      Possible duplicate
                    </span>
                  ) : null}
                </div>
                {file.warnings.length > 0 ? (
                  <p className="mt-1 max-w-sm text-xs text-terracotta-700">{file.warnings[0]}</p>
                ) : null}
                {file.error ? (
                  <p className="mt-1 max-w-sm text-xs text-terracotta-700">{file.error}</p>
                ) : null}
              </td>
              <td className="py-3 pr-3">
                <StatusBadge status={file.status} />
                <p className="mt-1 text-xs text-ink-500">
                  {file.statusDetail || formatWhen(file.updatedAt)}
                </p>
              </td>
              <td className="py-3 pr-3 text-ink-700">
                {file.pageCount > 0 ? `${file.pageCount} ${file.pageLabel.toLowerCase()}(s)` : '—'}
              </td>
              <td className="py-3 pr-3 text-ink-700">
                {file.language ? (
                  <span title="Language is inferred from the script used, not detected confidently">
                    {file.language}
                    {file.languageScript ? ` · ${file.languageScript}` : ''}
                  </span>
                ) : (
                  '—'
                )}
              </td>
              <td className="py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => onOpen(file.id)}
                    className="rounded-md border border-ivory-300 px-2 py-1 text-xs font-medium text-ink-700 hover:bg-ivory-100"
                  >
                    Open
                  </button>
                  <a
                    href={archiveFileDownloadUrl(file.id)}
                    className="rounded-md border border-ivory-300 px-2 py-1 text-xs font-medium text-ink-700 hover:bg-ivory-100"
                  >
                    Original
                  </a>
                  {file.kind !== 'zip' && (file.status === 'failed' || file.status === 'partial') ? (
                    <button
                      type="button"
                      onClick={() => onReprocess(file.id)}
                      disabled={actionBusyId === file.id}
                      className="rounded-md border border-teal-600 px-2 py-1 text-xs font-medium text-teal-700 hover:bg-teal-50 disabled:opacity-50"
                    >
                      {actionBusyId === file.id ? 'Queueing…' : 'Reprocess'}
                    </button>
                  ) : null}
                  {file.kind !== 'zip' ? (
                    <button
                      type="button"
                      onClick={() => onDelete(file.id)}
                      disabled={actionBusyId === file.id || isBusy(file.status)}
                      className="rounded-md px-2 py-1 text-xs font-medium text-terracotta-700 hover:bg-terracotta-50 disabled:opacity-50"
                    >
                      Remove
                    </button>
                  ) : null}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default ArchiveFileTable;
