import type { TimelineEvent } from '../../types/archive';

interface ArchiveTimelineProps {
  events: TimelineEvent[];
  limitations: string[];
  onOpenFile: (fileId: string) => void;
}

const PRECISION_LABELS: Record<string, string> = {
  exact: 'Exact in source',
  approximate: 'Approximate',
  century: 'Century',
  range: 'Range',
  unknown: 'Uncertain',
};

function yearPhrase(event: TimelineEvent): string | null {
  if (event.yearStart == null) return null;
  // A century with no era marker has CE-shaped bounds but no era: showing
  // them as CE would assert something the source did not say.
  if (event.era === 'unknown') return null;
  const label = (value: number) => (value < 0 ? `${Math.abs(value)} BCE` : `${value} CE`);
  if (event.yearEnd != null && event.yearEnd !== event.yearStart) {
    return `${label(event.yearStart)} – ${label(event.yearEnd)}`;
  }
  return label(event.yearStart);
}

/** Chronological view of the dated passages extracted from one archive. */
export function ArchiveTimeline({ events, limitations, onOpenFile }: ArchiveTimelineProps) {
  if (events.length === 0) {
    return (
      <div role="status" className="rounded-lg border border-dashed border-ivory-300 bg-ivory-50 px-4 py-6 text-sm text-ink-500">
        <p className="font-medium text-ink-700">No dated events found</p>
        <p className="mt-1">
          The extracted text of this archive contains no date the pipeline could read with
          confidence. Years are only extracted where the source states them or a construction
          verb dates them.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <ol className="relative flex flex-col gap-4 border-l-2 border-ivory-300 pl-5">
        {events.map((event) => {
          const phrase = yearPhrase(event);
          return (
            <li key={event.id} className="relative">
              <span
                aria-hidden="true"
                className="absolute -left-[1.65rem] top-1.5 h-3 w-3 rounded-full border-2 border-ivory bg-terracotta-500"
              />
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-display text-base font-semibold text-teal-800">
                  {event.dateText}
                </span>
                {phrase ? <span className="text-xs text-ink-500">{phrase}</span> : null}
                <span className="rounded-full bg-ivory-200 px-2 py-0.5 text-xs text-ink-700">
                  {PRECISION_LABELS[event.precision] ?? event.precision}
                </span>
              </div>
              <p className="mt-0.5 text-sm text-ink-900">{event.title}</p>
              {event.evidence && event.evidence !== event.title ? (
                <p className="mt-1 border-l-2 border-ivory-300 pl-2 text-xs italic text-ink-500">
                  “{event.evidence}”
                </p>
              ) : null}
              <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-ink-500">
                {event.sourceFile && event.fileId ? (
                  <button
                    type="button"
                    onClick={() => onOpenFile(event.fileId!)}
                    className="font-medium text-teal-700 underline-offset-2 hover:underline"
                  >
                    {event.sourceFile}
                  </button>
                ) : (
                  <span>Source file not recorded</span>
                )}
                {event.sourcePage ? <span>· page {event.sourcePage}</span> : null}
                <span>· {event.verificationStatus}</span>
              </p>
            </li>
          );
        })}
      </ol>
      {limitations.length > 0 ? (
        <ul className="list-inside list-disc text-xs text-ink-500">
          {limitations.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export default ArchiveTimeline;
