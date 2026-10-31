import type { ArchiveFileKind, ArchiveFileStatus } from '../../types/archive';

export const STATUS_LABELS: Record<ArchiveFileStatus, string> = {
  uploaded: 'Uploaded',
  queued: 'Queued',
  processing: 'Processing',
  ocr_completed: 'OCR completed',
  text_extracted: 'Text extracted',
  analysed: 'Analysed',
  completed: 'Completed',
  partial: 'Partial',
  failed: 'Failed',
};

export const STATUS_CLASSES: Record<ArchiveFileStatus, string> = {
  uploaded: 'bg-ivory-200 text-ink-700',
  queued: 'bg-ivory-200 text-ink-700',
  processing: 'bg-teal-100 text-teal-800',
  ocr_completed: 'bg-teal-100 text-teal-800',
  text_extracted: 'bg-teal-100 text-teal-800',
  analysed: 'bg-teal-100 text-teal-800',
  completed: 'bg-teal-600 text-ivory',
  partial: 'bg-terracotta-500 text-ivory',
  failed: 'bg-terracotta-600 text-ivory',
};

export const KIND_LABELS: Record<ArchiveFileKind, string> = {
  image: 'Image',
  pdf: 'PDF',
  docx: 'Word',
  pptx: 'Slides',
  text: 'Text',
  zip: 'ZIP',
};

export function isBusy(status: ArchiveFileStatus): boolean {
  return ['uploaded', 'queued', 'processing', 'ocr_completed', 'text_extracted', 'analysed'].includes(
    status,
  );
}

export function formatWhen(value: string | null | undefined): string {
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

export function StatusBadge({ status }: { status: ArchiveFileStatus }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${
        STATUS_CLASSES[status] ?? STATUS_CLASSES.uploaded
      }`}
    >
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

export function KindBadge({ kind }: { kind: ArchiveFileKind }) {
  return (
    <span className="inline-flex items-center rounded-full border border-ivory-300 bg-ivory-50 px-2 py-0.5 text-xs font-medium text-ink-700">
      {KIND_LABELS[kind] ?? kind}
    </span>
  );
}
