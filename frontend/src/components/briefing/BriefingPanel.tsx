import { KeyFacts } from '../subject/KeyFacts';
import type { HistoricalBriefing } from '../../types/api';

/**
 * R5/R6 — renders a grounded historical briefing.
 *
 * Every sentence in the quoted half is source material the backend selected
 * from a retrieved record, and each carries the citation number of that
 * record. The panel therefore never presents an unsourced claim: the wording
 * comes from `briefing.summary` / `explanation` / `keyFacts` / `timeline`,
 * which the backend builds by selecting sentences, not by generating them.
 *
 * Two things the panel is careful about:
 *
 * * `briefing.method` is displayed as-is, so the provenance of the text is
 *   visible rather than implied;
 * * `briefing.synthesis`, when present, is the *only* generated text and is
 *   rendered in its own labelled block, with the provider/model that wrote
 *   it — so a reader can always tell quoted material from composed prose.
 */

const METHOD_LABELS: Record<string, string> = {
  'extractive-quotes': 'Quoted from the retrieved sources',
};

const KIND_LABELS: Record<HistoricalBriefing['queryKind'], string> = {
  year: 'Year overview',
  named: 'Subject overview',
  general: 'Overview',
};

function Citation({ index }: { index: number }) {
  return (
    <span
      title={`Source ${index}`}
      className="ml-1 align-super text-[0.65rem] font-medium text-terracotta-600"
    >
      [{index}]
    </span>
  );
}

/** Sentences already shown elsewhere, so the explanation never repeats them. */
function duplicateLines(briefing: HistoricalBriefing): string[] {
  const lines: string[] = [];
  const push = (value?: string | null) => {
    if (!value) return;
    for (const sentence of value.split(/(?<=[.!?])\s+/)) {
      const cleaned = sentence.trim();
      if (cleaned) lines.push(cleaned.replace(/^Recorded events include:\s*/, ''));
    }
  };
  push(briefing.summary);
  for (const fact of briefing.keyFacts) push(fact.text);
  return lines;
}

function pruneExplanation(explanation: string, briefing: HistoricalBriefing): string {
  const seen = new Set(duplicateLines(briefing).map((line) => line.toLowerCase()));
  const paragraphs = explanation.split(/\n{2,}/);
  const kept: string[] = [];
  for (const paragraph of paragraphs) {
    const keptSentences = paragraph
      .split(/(?<=[.!?])\s+/)
      .map((sentence) => sentence.trim())
      .filter(Boolean)
      .filter((sentence) => {
        const key = sentence.toLowerCase();
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
    if (keptSentences.length > 0) kept.push(keptSentences.join(' '));
  }
  return kept.join('\n\n');
}

export interface BriefingPanelProps {
  briefing: HistoricalBriefing;
  /** Heading text; defaults to a page-appropriate label. */
  heading?: string;
  /**
   * Heading id. Distinct per page: the search page and the analysis page can
   * show their own briefing at the same time, and ids must stay unique.
   */
  headingId?: string;
  className?: string;
}

export function BriefingPanel({
  briefing,
  heading,
  headingId = 'briefing-heading',
  className,
}: BriefingPanelProps) {
  const explanation = briefing.explanation
    ? pruneExplanation(briefing.explanation, briefing)
    : null;
  const hasNarrative = Boolean(briefing.summary || explanation);
  const hasBody =
    hasNarrative ||
    briefing.timeline.length > 0 ||
    briefing.keyFacts.length > 0 ||
    briefing.people.length > 0 ||
    Boolean(briefing.synthesis);

  // Nothing worth quoting (e.g. a matched record with no text) → no panel.
  if (!hasBody && briefing.uncertainty.length === 0) return null;

  const methodLabel = METHOD_LABELS[briefing.method] ?? briefing.method;
  const sourceCount = briefing.sources.length;

  return (
    <section
      aria-labelledby={headingId}
      className={`rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm ${className ?? ''}`}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id={headingId} className="font-display text-lg text-teal-800">
          {heading ?? 'Historical explanation'}
        </h2>
        <span className="rounded-full bg-teal-100 px-2 py-0.5 text-xs font-medium text-teal-800">
          {KIND_LABELS[briefing.queryKind]}
        </span>
      </div>

      <p className="mt-1 text-xs text-ink-500">
        About “{briefing.query}” · {methodLabel}
        {sourceCount > 0
          ? ` · ${sourceCount === 1 ? '1 source' : `${sourceCount} sources`}`
          : ''}
        . The wording is the sources’ own, cited by number; it is not
        independently verified history.
      </p>

      {briefing.summary ? (
        <p className="mt-4 text-sm font-medium leading-relaxed text-ink-900">
          {briefing.summary}
        </p>
      ) : null}

      {explanation ? (
        <p className="mt-3 whitespace-pre-line text-sm leading-relaxed text-ink-700">
          {explanation}
        </p>
      ) : null}

      {briefing.timeline.length > 0 ? (
        <div className="mt-4">
          <h3 className="text-sm font-semibold text-teal-800">Dated events</h3>
          <ol className="mt-2 flex flex-col gap-2">
            {briefing.timeline.map((entry, index) => (
              <li
                key={`${entry.date}-${index}`}
                className="rounded-lg border border-ivory-200 bg-ivory-50 px-3 py-2 text-sm text-ink-700"
              >
                <span className="font-medium text-terracotta-600">{entry.date}</span>
                {' — '}
                {entry.description}
                <Citation index={entry.sourceIndex} />
              </li>
            ))}
          </ol>
        </div>
      ) : null}

      <KeyFacts
        facts={briefing.keyFacts}
        people={briefing.people}
        className="mt-4"
      />

      {briefing.synthesis ? (
        <div
          className="mt-4 rounded-lg border border-teal-200 bg-teal-50 px-3 py-3"
          data-testid="briefing-synthesis"
        >
          <h3 className="text-sm font-semibold text-teal-800">
            AI summary of the cited sources
          </h3>
          <p className="mt-1 text-xs text-ink-500">
            Written by {briefing.synthesis.provider}
            {briefing.synthesis.model ? ` (${briefing.synthesis.model})` : ''} over
            the statements above; every sentence cites them. Generated text — not
            a quotation.
          </p>
          <p className="mt-2 text-sm leading-relaxed text-ink-900">
            {briefing.synthesis.text}
          </p>
        </div>
      ) : null}

      {briefing.uncertainty.length > 0 ? (
        <div className="mt-4 rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2">
          <h3 className="text-sm font-semibold text-teal-800">
            Evidence and limitations
          </h3>
          <ul className="mt-1 list-disc pl-5 text-xs leading-relaxed text-ink-500">
            {briefing.uncertainty.map((note, index) => (
              <li key={`${note.slice(0, 24)}-${index}`}>{note}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

export default BriefingPanel;
