import { formatConfidence, pluralizeCount } from '../../utils/formatters';
import type { HistoricalBriefing } from '../../types/api';
import { BriefingPanel } from '../briefing/BriefingPanel';
import { RelatedTopics } from '../subject/RelatedTopics';

/** An entity to display. Primitive props only — no API contract types. */
export interface EntityView {
  name: string;
  type: string;
  /** 0..1 confidence when the backend reports it. */
  confidence?: number | null;
}

/** A matched historical record to display. */
export interface MatchView {
  title: string;
  /** Free-text year, e.g. "c. 1885". */
  year?: string | null;
  /** 0..1 retrieval-relevance score when the backend reports it. */
  score?: number | null;
  /** Origin of the record (archive, collection, ...). */
  source?: string | null;
  /** Canonical link to the cited source material, when one exists. */
  url?: string | null;
  /** A window of the record's own text around the match. */
  excerpt?: string | null;
  /** High/medium/low band for `score`. */
  relevance?: 'high' | 'medium' | 'low' | null;
}

/** Renders a record's source link when the backend provides one. */
function SourceLink({ url, source }: { url?: string | null; source?: string | null }) {
  if (!url) return source ? <span>{source}</span> : null;
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="font-medium text-teal-700 underline underline-offset-2 hover:text-teal-800"
    >
      {source ? `View source — ${source}` : 'View source'}
    </a>
  );
}

interface ExtractedEntitiesProps {
  extractedText?: string | null;
  /** AI-generated candidate reading — shown as a candidate, never as fact. */
  interpretation?: string | null;
  entities: EntityView[];
  matches: MatchView[];
  /** Grounded history of the identified entity, quoted from the sources. */
  briefing?: HistoricalBriefing | null;
  /**
   * Opens one of the briefing's related topics. The topics are identity-
   * backed (each carries its own query), so the same component that serves
   * the search page serves the analysis result here.
   */
  onOpenTopic?: (query: string) => void;
}

function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed border-ivory-300 bg-ivory-50 px-3 py-4 text-sm text-ink-500">
      {children}
    </p>
  );
}

/**
 * A3 — rendering of extraction output: the extracted text, the detected
 * entities with their types and confidence, and the matched historical
 * records. Every value comes in through props (adapted from the API
 * response by the page); nothing here is hardcoded data. Each section has
 * its own empty state so a partial extraction still renders usefully.
 */
export function ExtractedEntities({
  extractedText,
  interpretation,
  entities,
  matches,
  briefing,
  onOpenTopic,
}: ExtractedEntitiesProps) {
  const hasText = typeof extractedText === 'string' && extractedText.trim() !== '';
  const hasInterpretation =
    typeof interpretation === 'string' && interpretation.trim() !== '';
  const entityList = entities ?? [];
  const matchList = matches ?? [];

  return (
    <div className="mt-6 flex flex-col gap-6">
      {/* Extracted text */}
      <section aria-labelledby="extracted-text-heading" className="rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm">
        <h2 id="extracted-text-heading" className="font-display text-lg text-teal-800">
          Extracted text
        </h2>
        <div className="mt-3">
          {hasText ? (
            <p className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border border-ivory-200 bg-ivory-50 p-3 text-sm leading-relaxed text-ink-700">
              {extractedText}
            </p>
          ) : (
            <EmptyState>No text could be extracted from this image.</EmptyState>
          )}
        </div>
      </section>

      {/* AI reading — candidate interpretation, explicitly not a verified fact */}
      {hasInterpretation ? (
        <section aria-labelledby="interpretation-heading" className="rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm">
          <h2 id="interpretation-heading" className="font-display text-lg text-teal-800">
            AI reading of the image{' '}
            <span className="rounded-full bg-terracotta-100 px-2 py-0.5 align-middle text-xs font-medium text-terracotta-700">
              Candidate — not a verified identification
            </span>
          </h2>
          <p className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border border-ivory-200 bg-ivory-50 p-3 text-sm leading-relaxed text-ink-700">
            {interpretation}
          </p>
        </section>
      ) : null}

      {/* Detected entities */}
      <section aria-labelledby="entities-heading" className="rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm">
        <h2 id="entities-heading" className="font-display text-lg text-teal-800">
          Entities <span className="text-sm font-normal text-ink-500">({pluralizeCount(entityList.length, 'entity', 'entities')})</span>
        </h2>
        <div className="mt-3">
          {entityList.length === 0 ? (
            <EmptyState>No entities were detected in this image.</EmptyState>
          ) : (
            <ul className="flex flex-col gap-2">
              {entityList.map((entity, index) => (
                <li
                  key={`${entity.name}-${index}`}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-ivory-200 bg-ivory-50 px-3 py-2"
                >
                  <span className="flex items-center gap-2">
                    <span className="font-display font-medium text-teal-800">{entity.name}</span>
                    <span className="rounded-full bg-teal-100 px-2 py-0.5 text-xs font-medium text-teal-800">
                      {entity.type}
                    </span>
                  </span>
                  {entity.confidence !== null && entity.confidence !== undefined ? (
                    <span className="text-xs text-ink-500" title="Extraction confidence">
                      {formatConfidence(entity.confidence)}
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      {/* Historical context — quoted from the retrieved sources */}
      {briefing ? (
        <BriefingPanel
          briefing={briefing}
          heading="Historical context"
          headingId="analysis-briefing-heading"
        />
      ) : null}

      {/* Topics the subject connects to — the same real navigation the
          search page offers, so an image result is an entry point too. */}
      {briefing ? (
        <RelatedTopics
          topics={briefing.relatedTopics}
          fallbackTitles={briefing.related}
          onOpen={onOpenTopic}
          headingId="analysis-related-topics-heading"
        />
      ) : null}

      {/* Matched historical records */}
      <section aria-labelledby="matches-heading" className="rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm">
        <h2 id="matches-heading" className="font-display text-lg text-teal-800">
          Historical matches{' '}
          <span className="text-sm font-normal text-ink-500">
            ({pluralizeCount(matchList.length, 'match', 'matches')})
          </span>
        </h2>
        <div className="mt-3">
          {matchList.length === 0 ? (
            <EmptyState>No historical records matched this extraction.</EmptyState>
          ) : (
            <ul className="flex flex-col gap-2">
              {matchList.map((match, index) => (
                <li
                  key={`${match.title}-${index}`}
                  className="rounded-lg border border-ivory-200 bg-ivory-50 px-3 py-2"
                >
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className="font-medium text-ink-900">{match.title}</span>
                    {match.year ? (
                      <span className="text-sm text-terracotta-600">{match.year}</span>
                    ) : null}
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-500">
                    <SourceLink url={match.url} source={match.source} />
                    {match.relevance ? (
                      <span
                        title={
                          match.score !== null && match.score !== undefined
                            ? `Retrieval relevance ${match.score.toFixed(2)} — how well the extraction matches this record's text, not a confidence that the history is verified.`
                            : undefined
                        }
                      >
                        {match.relevance === 'high'
                          ? 'High relevance'
                          : match.relevance === 'medium'
                            ? 'Medium relevance'
                            : 'Low relevance'}
                      </span>
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>
    </div>
  );
}

export default ExtractedEntities;
