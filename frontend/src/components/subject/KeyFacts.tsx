import type { KeyFact } from '../../types/api';

/**
 * R6 — key facts, quoted from the cited sources.
 *
 * Each fact is a sentence the backend selected verbatim from a retrieved
 * source, so the citation number next to it is meaningful. A month later the
 * reader can open source N and find the same sentence. Nothing here is
 * summarised, inferred or embellished; when a subject has no facts the block
 * is not rendered at all rather than padded with guesses.
 */

export interface KeyFactsProps {
  facts: KeyFact[];
  people?: string[];
  className?: string;
}

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

export function KeyFacts({ facts, people = [], className }: KeyFactsProps) {
  if (facts.length === 0 && people.length === 0) return null;

  return (
    <div className={`flex flex-col gap-4 ${className ?? ''}`}>
      {facts.length > 0 ? (
        <div>
          <h3 className="text-sm font-semibold text-teal-800">Key facts</h3>
          <ul className="mt-2 list-disc pl-5 text-sm leading-relaxed text-ink-700">
            {facts.map((fact, index) => (
              <li key={`${fact.text.slice(0, 24)}-${index}`}>
                {fact.text}
                <Citation index={fact.sourceIndex} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {people.length > 0 ? (
        <div>
          <h3 className="text-sm font-semibold text-teal-800">People recorded</h3>
          <ul className="mt-2 flex flex-wrap gap-2">
            {people.map((person, index) => (
              <li
                key={`${person}-${index}`}
                className="rounded-full border border-ivory-300 bg-ivory-50 px-3 py-1 text-xs text-ink-700"
              >
                {person}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export default KeyFacts;
