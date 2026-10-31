import { useEffect, useState } from 'react';

import { useAuth } from '../../hooks/useAuth';
import { ApiError } from '../../services/api';
import {
  askStories,
  getStory,
  submitStory,
  type StorySubmissionInput,
} from '../../services/archiveApi';
import type { StoryAskResponse, StoryDetail, StorySource, StoryVersion } from '../../types/archive';

interface StoryReaderProps {
  storyId: string;
  /** Return to the site's details panel on the map. */
  onBack: () => void;
  onOpenStory: (storyId: string) => void;
}

type ReaderState =
  | { phase: 'loading' }
  | { phase: 'error'; message: string }
  | { phase: 'ready'; story: StoryDetail };

const LANGUAGES: Array<[string, string]> = [
  ['en', 'English'],
  ['hi', 'Hindi'],
  ['gu', 'Gujarati'],
  ['sa', 'Sanskrit'],
  ['bn', 'Bengali'],
  ['mr', 'Marathi'],
  ['ta', 'Tamil'],
  ['te', 'Telugu'],
  ['kn', 'Kannada'],
  ['ml', 'Malayalam'],
  ['or', 'Odia'],
  ['pa', 'Punjabi'],
  ['as', 'Assamese'],
  ['ur', 'Urdu'],
];

function sourceTypeLabel(sourceType: string): string {
  return sourceType
    .split('-')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function VerificationNote({ story }: { story: StoryDetail }) {
  if (story.isCommunity) {
    return (
      <p className="rounded-md border border-terracotta-300 bg-terracotta-50 px-3 py-2 text-xs text-terracotta-800">
        Community account, approved for publication. It records a tradition
        held by its contributor; it is not a verified historical claim.
      </p>
    );
  }
  if (story.verificationStatus === 'unverified') {
    return (
      <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
        This record is an unverified tradition. It is reproduced with its
        sources so it can be read and weighed, not presented as established
        history.
      </p>
    );
  }
  return (
    <p className="rounded-md border border-ivory-300 bg-ivory-50 px-3 py-2 text-xs text-ink-600">
      Sources were checked by the compiler for this summary. That is a check of
      the record, not proof that the narrative is historically true.
    </p>
  );
}

function SourceList({ sources }: { sources: StorySource[] }) {
  if (sources.length === 0) {
    return (
      <p className="text-xs text-ink-500">
        No source is recorded for this account. That is itself a limitation.
      </p>
    );
  }
  return (
    <ol className="mt-1 space-y-1.5">
      {sources.map((source) => (
        <li key={source.id} className="text-xs leading-relaxed text-ink-700">
          <span className="font-medium">{source.title}</span>
          {source.author ? <span> — {source.author}</span> : null}
          <span className="text-ink-500"> · {sourceTypeLabel(source.sourceType)}</span>
          {source.published ? <span className="text-ink-500"> · {source.published}</span> : null}
          {source.locator ? <span className="text-ink-500"> · {source.locator}</span> : null}
          {source.url ? (
            <>
              {' · '}
              <a
                href={source.url}
                target="_blank"
                rel="noreferrer noopener"
                className="text-teal-700 underline decoration-teal-300 underline-offset-2 hover:text-teal-900"
              >
                Open source
              </a>
            </>
          ) : null}
          {source.licence ? <div className="text-[11px] text-ink-500">{source.licence}</div> : null}
          {source.excerpt ? (
            <blockquote className="mt-1 border-l-2 border-ivory-300 pl-2 text-[11px] italic text-ink-600">
              {source.excerpt}
            </blockquote>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

function VersionCard({ version }: { version: StoryVersion }) {
  return (
    <article className="rounded-lg border border-ivory-300 bg-ivory-50 p-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <h4 className="font-display text-sm font-semibold text-teal-900">{version.label}</h4>
        <span className="rounded-full border border-ivory-300 bg-ivory px-1.5 py-0.5 text-[10px] text-ink-600">
          {version.verificationStatus}
        </span>
        <span className="text-[10px] uppercase tracking-wide text-ink-500">{version.language}</span>
      </div>
      {version.tradition ? (
        <p className="mt-1 text-[11px] text-ink-500">{version.tradition}</p>
      ) : null}
      {version.community ? (
        <p className="text-[11px] text-ink-500">Community: {version.community}</p>
      ) : null}
      <p className="mt-1.5 text-xs leading-relaxed text-ink-700">{version.summary}</p>
      {version.narrative ? (
        <p className="mt-1 text-xs leading-relaxed text-ink-700">{version.narrative}</p>
      ) : null}
      {version.differences ? (
        <p className="mt-1.5 rounded-md bg-ivory px-2 py-1 text-[11px] text-ink-600">
          <span className="font-medium text-ink-700">Differs from the other accounts: </span>
          {version.differences}
        </p>
      ) : null}
      <div className="mt-2">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">Sources</p>
        <SourceList sources={version.sources} />
      </div>
    </article>
  );
}

/**
 * Dedicated story reading view.
 *
 * One story, its full narrative, its sources, its separate traditions and
 * the evidence question it can answer. It never refuses to show a story
 * because a single "canonical" account is missing: alternative versions sit
 * beside the main narrative, each with its own provenance.
 */
export function StoryReader({ storyId, onBack, onOpenStory }: StoryReaderProps) {
  const [state, setState] = useState<ReaderState>({ phase: 'loading' });
  const auth = useAuth();

  const [question, setQuestion] = useState('');
  const [askBusy, setAskBusy] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const [askResult, setAskResult] = useState<StoryAskResponse | null>(null);

  const [form, setForm] = useState<StorySubmissionInput>({
    title: '',
    narrative: '',
    language: 'en',
    community: '',
    tradition: '',
    sourceNote: '',
    interviewDate: '',
    consent: false,
  });
  const [submitBusy, setSubmitBusy] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setState({ phase: 'loading' });
    setAskResult(null);
    setAskError(null);
    setQuestion('');
    getStory(storyId).then(
      (story) => {
        if (!cancelled) setState({ phase: 'ready', story });
      },
      (err: unknown) => {
        if (!cancelled) {
          setState({
            phase: 'error',
            message:
              err instanceof ApiError ? err.message : 'Could not load this story.',
          });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [storyId]);

  if (state.phase === 'loading') {
    return (
      <p role="status" className="rounded-xl border border-ivory-300 bg-ivory p-5 text-sm text-ink-700">
        Loading the story…
      </p>
    );
  }

  if (state.phase === 'error') {
    return (
      <div className="rounded-xl border border-ivory-300 bg-ivory p-5">
        <p role="alert" className="text-sm text-terracotta-700">{state.message}</p>
        <button
          type="button"
          onClick={onBack}
          className="mt-3 rounded-lg border border-ivory-300 px-3 py-1.5 text-sm font-medium text-ink-700 hover:bg-ivory-100"
        >
          Back to the site
        </button>
      </div>
    );
  }

  const story = state.story;
  const paragraphs = story.narrative.split(/\n{2,}/).filter(Boolean);

  const ask = async () => {
    const trimmed = question.trim();
    if (trimmed.length < 3) return;
    setAskBusy(true);
    setAskError(null);
    try {
      setAskResult(await askStories(story.siteId, trimmed));
    } catch (err) {
      setAskError(
        err instanceof ApiError ? err.message : 'Could not answer from the story library.',
      );
    } finally {
      setAskBusy(false);
    }
  };

  const contribute = async () => {
    setSubmitError(null);
    if (!form.consent) {
      setSubmitError('Please confirm consent before submitting.');
      return;
    }
    setSubmitBusy(true);
    try {
      const result = await submitStory(story.siteId, form);
      setSubmitted(result.message);
    } catch (err) {
      setSubmitError(
        err instanceof ApiError ? err.message : 'Could not submit this contribution.',
      );
    } finally {
      setSubmitBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <button
          type="button"
          onClick={onBack}
          className="rounded-lg border border-ivory-300 px-3 py-1.5 text-sm font-medium text-ink-700 hover:bg-ivory-100"
        >
          ← Back to {story.siteName}
        </button>
        <span className="text-xs text-ink-500">
          {story.siteName}
          {story.siteState ? ` · ${story.siteState}` : ''}
        </span>
      </div>

      <article className="rounded-xl border border-ivory-300 bg-ivory p-5">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="rounded-full bg-teal-600 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-ivory">
            {story.categoryLabel}
          </span>
          <span className="rounded-full border border-ivory-300 bg-ivory-50 px-2 py-0.5 text-[10px] text-ink-700">
            {story.narrativeTypeLabel}
          </span>
          <span className="text-[10px] uppercase tracking-wide text-ink-500">
            {story.languageLabel}
          </span>
        </div>
        <h2 className="mt-2 font-display text-2xl font-semibold text-teal-900">{story.title}</h2>
        <p className="mt-1 text-sm leading-relaxed text-ink-700">{story.summary}</p>

        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-500">
          {story.tradition ? <span>Tradition: {story.tradition}</span> : null}
          {story.community ? <span>Community: {story.community}</span> : null}
          {story.period ? <span>Period: {story.period}</span> : null}
          {story.provenance ? <span>Provenance: {story.provenance}</span> : null}
        </div>

        <div className="mt-3">
          <VerificationNote story={story} />
        </div>

        <div className="mt-4 space-y-3 text-sm leading-relaxed text-ink-800">
          {paragraphs.map((paragraph, index) => (
            <p key={index}>{paragraph}</p>
          ))}
        </div>

        {story.culturalBackground ? (
          <section className="mt-4 rounded-lg bg-ivory-50 p-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-teal-800">
              Cultural background
            </h3>
            <p className="mt-1 text-sm leading-relaxed text-ink-700">{story.culturalBackground}</p>
          </section>
        ) : null}

        {story.versions.length > 0 ? (
          <section className="mt-5" aria-label="Alternative traditions">
            <h3 className="font-display text-lg font-semibold text-teal-800">
              Alternative traditions
            </h3>
            <p className="mt-0.5 text-xs leading-relaxed text-ink-600">
              These are separate accounts of the same story. They are not merged
              into one narrative, and none is presented as the definitive
              version.
            </p>
            <div className="mt-2 space-y-2">
              {story.versions.map((version) => (
                <VersionCard key={version.id} version={version} />
              ))}
            </div>
          </section>
        ) : null}

        <section className="mt-5" aria-label="Sources and evidence">
          <h3 className="font-display text-lg font-semibold text-teal-800">Sources &amp; evidence</h3>
          <SourceList sources={story.sources} />
        </section>

        {story.relatedStories.length > 0 ? (
          <section className="mt-5" aria-label="Related stories">
            <h3 className="font-display text-lg font-semibold text-teal-800">More stories here</h3>
            <ul className="mt-1 divide-y divide-ivory-200">
              {story.relatedStories.map((related) => (
                <li key={related.id} className="py-1.5">
                  <button
                    type="button"
                    onClick={() => onOpenStory(related.id)}
                    className="text-left text-sm font-medium text-teal-700 hover:underline"
                  >
                    {related.title}
                  </button>
                  <span className="ml-2 text-[11px] text-ink-500">{related.categoryLabel}</span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </article>

      <section
        aria-label="Ask about this place's stories"
        className="rounded-xl border border-ivory-300 bg-ivory p-5"
      >
        <h3 className="font-display text-lg font-semibold text-teal-800">
          Ask about {story.siteName}
        </h3>
        <p className="mt-0.5 text-xs text-ink-600">
          Answers come only from the stories stored for this place. If the
          library does not cover the question, it says so.
        </p>
        <div className="mt-2 flex flex-col gap-2 sm:flex-row">
          <input
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !askBusy) void ask();
            }}
            aria-label="Ask about this place's stories"
            placeholder="e.g. Are there multiple versions of the origin story?"
            maxLength={400}
            className="w-full rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-1.5 text-sm text-ink-900 placeholder:text-ink-500 focus:border-teal-500 focus:outline-none"
          />
          <button
            type="button"
            onClick={() => void ask()}
            disabled={askBusy || question.trim().length < 3}
            className="shrink-0 rounded-lg bg-teal-600 px-3 py-1.5 text-sm font-medium text-ivory hover:bg-teal-700 disabled:opacity-50"
          >
            {askBusy ? 'Searching…' : 'Ask'}
          </button>
        </div>
        {askError ? (
          <p role="alert" className="mt-2 text-xs text-terracotta-700">{askError}</p>
        ) : null}
        {askResult ? (
          <div className="mt-3 rounded-lg bg-ivory-50 p-3" data-testid="story-answer">
            <p className="whitespace-pre-line text-sm leading-relaxed text-ink-800">
              {askResult.answer}
            </p>
            {askResult.citations.length > 0 ? (
              <div className="mt-2">
                <p className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">
                  Sources consulted
                </p>
                <ul className="mt-0.5 list-disc pl-4 text-[11px] text-ink-600">
                  {askResult.citations.map((citation) => (
                    <li key={citation}>{citation}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {askResult.limitation ? (
              <p className="mt-2 text-[11px] italic text-ink-500">{askResult.limitation}</p>
            ) : null}
          </div>
        ) : null}
      </section>

      <section
        aria-label="Contribute a local story"
        className="rounded-xl border border-ivory-300 bg-ivory p-5"
      >
        <h3 className="font-display text-lg font-semibold text-teal-800">
          Contribute a local story or oral history
        </h3>
        {submitted ? (
          <p role="status" className="mt-2 rounded-lg border border-teal-300 bg-teal-50 px-3 py-2 text-sm text-teal-900">
            {submitted}
          </p>
        ) : (
          <>
            <p className="mt-0.5 text-xs leading-relaxed text-ink-600">
              Contributions are stored as pending and reviewed by a moderator
              before they appear. Publication marks them as a community
              account, not as verified history.
            </p>
            {auth.phase !== 'authenticated' ? (
              <p className="mt-2 rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2 text-xs text-ink-600">
                Sign in from the header to submit a story. Contributions are
                always attributed only with the contributor's consent.
              </p>
            ) : (
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                <label className="flex flex-col gap-1 text-xs font-medium text-ink-700">
                  Story title
                  <input
                    value={form.title}
                    onChange={(event) => setForm({ ...form, title: event.target.value })}
                    className="rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-1.5 text-sm font-normal text-ink-900 focus:border-teal-500 focus:outline-none"
                  />
                </label>
                <label className="flex flex-col gap-1 text-xs font-medium text-ink-700">
                  Language
                  <select
                    value={form.language}
                    onChange={(event) => setForm({ ...form, language: event.target.value })}
                    className="rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-1.5 text-sm font-normal text-ink-900 focus:border-teal-500 focus:outline-none"
                  >
                    {LANGUAGES.map(([code, label]) => (
                      <option key={code} value={code}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1 text-xs font-medium text-ink-700 sm:col-span-2">
                  The narrative
                  <textarea
                    value={form.narrative}
                    onChange={(event) => setForm({ ...form, narrative: event.target.value })}
                    rows={5}
                    className="rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-1.5 text-sm font-normal text-ink-900 focus:border-teal-500 focus:outline-none"
                  />
                </label>
                <label className="flex flex-col gap-1 text-xs font-medium text-ink-700">
                  Community or tradition (optional)
                  <input
                    value={form.community ?? ''}
                    onChange={(event) => setForm({ ...form, community: event.target.value })}
                    className="rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-1.5 text-sm font-normal text-ink-900 focus:border-teal-500 focus:outline-none"
                  />
                </label>
                <label className="flex flex-col gap-1 text-xs font-medium text-ink-700">
                  Interview date (optional)
                  <input
                    value={form.interviewDate ?? ''}
                    onChange={(event) => setForm({ ...form, interviewDate: event.target.value })}
                    placeholder="e.g. 1998"
                    className="rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-1.5 text-sm font-normal text-ink-900 focus:border-teal-500 focus:outline-none"
                  />
                </label>
                <label className="flex flex-col gap-1 text-xs font-medium text-ink-700 sm:col-span-2">
                  Source or interview details (optional)
                  <input
                    value={form.sourceNote ?? ''}
                    onChange={(event) => setForm({ ...form, sourceNote: event.target.value })}
                    className="rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-1.5 text-sm font-normal text-ink-900 focus:border-teal-500 focus:outline-none"
                  />
                </label>
                <label className="flex items-start gap-2 text-xs text-ink-700 sm:col-span-2">
                  <input
                    type="checkbox"
                    checked={form.consent}
                    onChange={(event) => setForm({ ...form, consent: event.target.checked })}
                    className="mt-0.5"
                  />
                  I have the contributor's permission to share this account, and
                  I understand it will be labelled as a community contribution.
                </label>
                {submitError ? (
                  <p role="alert" className="text-xs text-terracotta-700 sm:col-span-2">
                    {submitError}
                  </p>
                ) : null}
                <div className="sm:col-span-2">
                  <button
                    type="button"
                    onClick={() => void contribute()}
                    disabled={submitBusy || form.title.trim().length < 3 || form.narrative.trim().length < 20}
                    className="rounded-lg bg-terracotta-600 px-3 py-1.5 text-sm font-medium text-ivory hover:bg-terracotta-700 disabled:opacity-50"
                  >
                    {submitBusy ? 'Submitting…' : 'Submit for review'}
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}

export default StoryReader;
