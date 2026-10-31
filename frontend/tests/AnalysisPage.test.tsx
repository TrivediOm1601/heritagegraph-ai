import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { AnalysisPage } from '../src/pages/AnalysisPage';
import { HomePage } from '../src/pages/HomePage';

/** Minimal Response stand-in: api.ts only reads ok/status/json/text. */
function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

const SUCCESS_FIXTURE = {
  id: 'an-1',
  status: 'completed',
  extractedText: 'Inscription: In memory of Ada Lovelace, 1815–1852.',
  entities: [
    { id: 'e1', name: 'Ada Lovelace', type: 'person', confidence: 0.92 },
    { id: 'e2', name: '1815', type: 'date', confidence: 0.78 },
  ],
  matches: [
    { id: 'm1', title: 'Ada Lovelace correspondence', year: 'c. 1843', score: 0.88, source: 'British Library' },
    { id: 'm2', title: 'Analytical Engine notes', year: '1843', score: 0.61 },
  ],
  warnings: [],
  error: null,
};

const EMPTY_FIXTURE = {
  id: 'an-2',
  status: 'completed',
  extractedText: '',
  entities: [],
  matches: [],
  warnings: [],
  error: null,
};

/**
 * A completed run whose briefing resolved a candidate subject with a real
 * photograph and one identity-backed related topic (the shape the backend
 * actually stores: MediaWiki page image + Commons metadata).
 */
const SUBJECT_FIXTURE = {
  id: 'an-subject',
  status: 'completed',
  extractedText: null,
  interpretation: 'Tiers of carved stone panels descend into a stepped well.',
  entities: [{ id: 'e1', name: 'Rani ki Vav', type: 'place', confidence: 0.72 }],
  matches: [],
  briefing: {
    query: 'Rani ki Vav',
    queryKind: 'named',
    method: 'extractive-quotes',
    summary: 'Rani ki Vav is a stepwell in Patan, Gujarat.',
    explanation: null,
    keyFacts: [],
    timeline: [],
    people: [],
    related: [],
    relatedTopics: [
      {
        id: 'wiki:900',
        name: 'Patan, Gujarat',
        type: 'place',
        description: 'city in Gujarat, India',
        query: 'Patan, Gujarat',
        image: null,
      },
    ],
    subject: {
      name: 'Rani ki Vav',
      type: 'place',
      description: 'stepwell in Patan, Gujarat, India',
      sourceUrl: 'https://en.wikipedia.org/wiki/Rani_ki_Vav',
      image: {
        url: 'https://upload.example.org/rani-ki-vav.jpg',
        altText: 'Rani ki Vav: stepwell in Patan, Gujarat, India',
        credit: 'Example Photographer',
        license: 'CC BY-SA 4.0',
        licenseUrl: 'https://creativecommons.org/licenses/by-sa/4.0/',
        filePageUrl: 'https://commons.example.org/wiki/File:Rani_ki_Vav.jpg',
      },
    },
    sources: [],
    uncertainty: [
      'This explanation describes the retrieved record for \u201cRani ki Vav\u201d. It does not confirm that the uploaded image depicts it.',
    ],
  },
  warnings: [],
  error: null,
};

const fetchMock = vi.fn();

describe('AnalysisPage', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockReset();
    window.history.replaceState({}, '', '/');
    // Fallback route: an empty graph for the A4 graph endpoint, 404 for
    // anything else (e.g. evidence requests).
    fetchMock.mockImplementation((url: unknown) => {
      const target = String(url);
      if (target.endsWith('/graph')) {
        return Promise.resolve(
          jsonResponse({ analysisId: 'an-test', nodes: [], edges: [] }, 200),
        );
      }
      return Promise.resolve(jsonResponse({ error: 'not found' }, 404));
    });
  });

  it('renders text, entities and matches from a successful response', async () => {
    fetchMock.mockResolvedValue(jsonResponse(SUCCESS_FIXTURE, 200));

    render(<AnalysisPage analysisId="an-1" />);

    // Extracted text comes from the response.
    expect(
      await screen.findByText(/In memory of Ada Lovelace, 1815–1852\./),
    ).toBeInTheDocument();

    // Entities with their types and confidence.
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument();
    expect(screen.getByText('person')).toBeInTheDocument();
    expect(screen.getByText('92%')).toBeInTheDocument();
    expect(screen.getByText('1815')).toBeInTheDocument();
    expect(screen.getByText('date')).toBeInTheDocument();

    // Matched historical records.
    expect(screen.getByText('Ada Lovelace correspondence')).toBeInTheDocument();
    expect(screen.getByText('c. 1843')).toBeInTheDocument();
    expect(screen.getByText('British Library')).toBeInTheDocument();
    expect(screen.getByText('Analytical Engine notes')).toBeInTheDocument();

    // Completed badge, no warnings.
    expect(screen.getByText('Completed')).toBeInTheDocument();
    expect(screen.queryByText(/^Warnings/)).not.toBeInTheDocument();

    // Requested via the agreed endpoints: analysis first, then its graph.
    // The graph request is issued from a passive effect, so wait for it
    // instead of assuming it already fired.
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(String(fetchMock.mock.calls[0][0])).toContain('/api/analysis/an-1');
    expect(String(fetchMock.mock.calls[1][0])).toContain('/api/analysis/an-1/graph');
    // The graph fixture is empty, so the graph section shows its empty state.
    expect(await screen.findByText('No relationships to display yet.')).toBeInTheDocument();
  });

  it('renders the grounded historical briefing for the identified entity', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        {
          ...SUCCESS_FIXTURE,
          id: 'an-brief',
          briefing: {
            query: 'Ada Lovelace',
            queryKind: 'named',
            method: 'extractive-quotes',
            summary: 'Ada Lovelace was an English mathematician.',
            explanation:
              'She is chiefly known for work on Charles Babbage\u2019s Analytical Engine.',
            keyFacts: [
              { text: 'She is chiefly known for work on the Analytical Engine.', sourceIndex: 1 },
            ],
            timeline: [],
            people: [],
            related: ['Charles Babbage'],
            sources: [
              {
                index: 1,
                id: 'wiki:1',
                title: 'Ada Lovelace',
                publisher: 'Wikipedia',
                relevance: 0.98,
              },
            ],
            uncertainty: [
              'This explanation describes the retrieved record; it does not confirm the image depicts it.',
            ],
          },
        },
        200,
      ),
    );

    render(<AnalysisPage analysisId="an-brief" />);

    expect(await screen.findByText('Historical context')).toBeInTheDocument();
    expect(screen.getByText(/Ada Lovelace was an English mathematician/)).toBeInTheDocument();
    expect(screen.getByText('Key facts')).toBeInTheDocument();
    // The uncertainty of an image identification stays visible.
    expect(
      screen.getByText(/does not confirm the image depicts it/),
    ).toBeInTheDocument();
    // …and the wording is labelled as quoted, not generated.
    expect(screen.getByText(/Quoted from the retrieved sources/)).toBeInTheDocument();
  });

  it('shows the candidate subject photograph and its related topics', async () => {
    fetchMock.mockResolvedValue(jsonResponse(SUBJECT_FIXTURE, 200));

    render(<AnalysisPage analysisId="an-subject" />);

    // The photograph the analysis resolved for the subject, served through
    // the same-origin proxy (B5)…
    const photo = await screen.findByRole('img', { name: /Rani ki Vav/ });
    expect(photo).toHaveAttribute(
      'src',
      `/api/media?url=${encodeURIComponent('https://upload.example.org/rani-ki-vav.jpg')}`,
    );
    // …carrying its attribution and license, like everywhere else.
    expect(screen.getByText(/Example Photographer/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'CC BY-SA 4.0' })).toHaveAttribute(
      'href',
      'https://creativecommons.org/licenses/by-sa/4.0/',
    );
    // The identification stays labelled a candidate of the image reading.
    expect(screen.getByText('Candidate subject — from this image')).toBeInTheDocument();

    // The related topic is a real link to the subject route, not a label.
    const topic = await screen.findByTestId('related-topic');
    expect(topic).toHaveTextContent('Patan, Gujarat');
    expect(topic).toHaveTextContent('city in Gujarat, India');
    expect(topic).toHaveAttribute('href', '?q=Patan%2C%20Gujarat');

    // Clicking it opens that subject in place (no reload, one history entry).
    fireEvent.click(topic);
    expect(new URLSearchParams(window.location.search).get('q')).toBe('Patan, Gujarat');
  });

  it("opens a related topic from the image result in the search page's subject view", async () => {
    fetchMock.mockImplementation((url: unknown) => {
      const target = String(url);
      if (target.endsWith('/graph')) {
        return Promise.resolve(
          jsonResponse({ analysisId: 'an-subject', nodes: [], edges: [] }, 200),
        );
      }
      if (target.includes('/api/search')) {
        // Answered only for the topic the click asked for, so the assertion
        // below can only pass if the *right* subject was fetched.
        return Promise.resolve(
          jsonResponse(
            {
              query: 'Patan, Gujarat',
              results: [],
              briefing: {
                query: 'Patan, Gujarat',
                queryKind: 'named',
                method: 'extractive-quotes',
                summary: 'Patan is a city in Gujarat, India.',
                explanation: null,
                keyFacts: [],
                timeline: [],
                people: [],
                related: [],
                relatedTopics: [],
                subject: {
                  name: 'Patan, Gujarat',
                  type: 'place',
                  description: 'city in Gujarat, India',
                  image: null,
                },
                sources: [],
                uncertainty: [],
              },
            },
            200,
          ),
        );
      }
      return Promise.resolve(jsonResponse(SUBJECT_FIXTURE, 200));
    });

    render(
      <>
        <HomePage />
        <AnalysisPage analysisId="an-subject" />
      </>,
    );

    fireEvent.click(await screen.findByTestId('related-topic'));

    // The topic's own overview replaces the prompt in the search section…
    expect(
      await screen.findByRole('heading', { name: 'Patan, Gujarat' }),
    ).toBeInTheDocument();
    // …and the analysis that pointed there is untouched.
    expect(screen.getByRole('heading', { name: 'Rani ki Vav' })).toBeInTheDocument();
    const lastCall = fetchMock.mock.calls[fetchMock.mock.calls.length - 1];
    expect(String(lastCall[0])).toContain('/api/search?query=Patan%2C%20Gujarat');
  });

  it('omits the briefing section when the backend sends none', async () => {
    fetchMock.mockResolvedValue(jsonResponse(SUCCESS_FIXTURE, 200));

    render(<AnalysisPage analysisId="an-1" />);

    await screen.findByText(/In memory of Ada Lovelace/);
    expect(screen.queryByText('Historical context')).not.toBeInTheDocument();
  });

  it('shows useful empty states for a completed analysis with no results', async () => {
    fetchMock.mockResolvedValue(jsonResponse(EMPTY_FIXTURE, 200));

    render(<AnalysisPage analysisId="an-2" />);

    expect(await screen.findByText('Completed')).toBeInTheDocument();
    expect(screen.getByText('No text could be extracted from this image.')).toBeInTheDocument();
    expect(screen.getByText('No entities were detected in this image.')).toBeInTheDocument();
    expect(
      screen.getByText('No historical records matched this extraction.'),
    ).toBeInTheDocument();

    // An empty result is not an error.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('renders a partial extraction with warnings and progress', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        {
          id: 'an-3',
          status: 'processing',
          progress: 0.6,
          extractedText: 'Partially legible caption mentioning Brighton.',
          entities: [{ id: 'e1', name: 'Brighton', type: 'place', confidence: 0.55 }],
          matches: [],
          warnings: ['Low contrast detected; some text may be missing.'],
          error: null,
        },
        200,
      ),
    );

    render(<AnalysisPage analysisId="an-3" />);

    expect(await screen.findByText('Processing')).toBeInTheDocument();
    expect(screen.getByText(/Low contrast detected/)).toBeInTheDocument();

    const progressBar = screen.getByRole('progressbar', { name: 'Analysis progress' });
    expect(progressBar).toHaveAttribute('aria-valuenow', '60');

    expect(screen.getByText('Brighton')).toBeInTheDocument();
    expect(screen.getByText('55%')).toBeInTheDocument();
    // Missing sections get their own empty state, not an error.
    expect(
      screen.getByText('No historical records matched this extraction.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows the backend error for a failed analysis', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        {
          id: 'an-4',
          status: 'failed',
          extractedText: null,
          entities: null,
          matches: null,
          warnings: null,
          error: 'OCR could not read this image.',
        },
        200,
      ),
    );

    render(<AnalysisPage analysisId="an-4" />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The analysis failed.');
    expect(alert).toHaveTextContent('OCR could not read this image.');
    expect(screen.getByText('Failed')).toBeInTheDocument();
    // No success wording anywhere.
    expect(screen.queryByText('Completed')).not.toBeInTheDocument();
  });

  it('shows an error with retry when the request fails, then recovers', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ error: 'database offline' }, 503))
      .mockResolvedValueOnce(jsonResponse(SUCCESS_FIXTURE, 200));

    render(<AnalysisPage analysisId="an-5" />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Could not load the analysis.');
    expect(alert).toHaveTextContent('database offline');

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));

    expect(await screen.findByText(/In memory of Ada Lovelace/)).toBeInTheDocument();
    // Failed attempt + retry + the follow-up graph fetch.
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('polls a processing analysis until it reaches a terminal state', async () => {
    let polls = 0;
    fetchMock.mockImplementation((url: unknown) => {
      const target = String(url);
      if (target.endsWith('/graph')) {
        return Promise.resolve(
          jsonResponse({ analysisId: 'an-6', nodes: [], edges: [] }, 200),
        );
      }
      polls += 1;
      const body =
        polls < 3
          ? {
              id: 'an-6',
              status: 'processing',
              progress: 0.4,
              extractedText: null,
              entities: [],
              matches: [],
              warnings: [],
              error: null,
            }
          : { ...SUCCESS_FIXTURE, id: 'an-6' };
      return Promise.resolve(jsonResponse(body, 200));
    });

    render(<AnalysisPage analysisId="an-6" />);

    // First response is still processing…
    expect(await screen.findByText('Processing')).toBeInTheDocument();

    // …and the poll loop keeps re-fetching until the terminal state,
    // then shows the completed extraction (never stuck on "Processing").
    expect(await screen.findByText('Completed', {}, { timeout: 5000 })).toBeInTheDocument();
    expect(await screen.findByText(/In memory of Ada Lovelace/)).toBeInTheDocument();
    expect(polls).toBeGreaterThanOrEqual(3);
  }, 10_000);

  it('shows the empty state when no analysis has been submitted yet', () => {
    fetchMock.mockResolvedValue(jsonResponse(SUCCESS_FIXTURE, 200));

    render(<AnalysisPage analysisId={null} />);

    expect(screen.getByText('No analysis yet')).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
