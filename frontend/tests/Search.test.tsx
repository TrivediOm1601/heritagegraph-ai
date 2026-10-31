import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';

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

const fetchMock = vi.fn();

function searchFor(query: string): void {
  fireEvent.change(screen.getByLabelText('Search terms'), { target: { value: query } });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
}

describe('HomePage search', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockReset();
    // R6: the subject route lives in the URL, so each test starts from a
    // clean location — otherwise the previous test's `?q=` would open a
    // subject on mount.
    window.history.replaceState({}, '', '/');
  });

  it('shows results for the submitted query, and different results for a different query', async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(
          {
            query: 'Ada Lovelace',
            results: [
              {
                id: 'r1',
                title: 'Ada Lovelace correspondence',
                year: 'c. 1843',
                source: 'British Library',
                url: 'https://example.org/correspondence',
                score: 0.91,
                excerpt: 'Letters discussing the Analytical Engine.',
                relevance: 'high',
              },
            ],
            briefing: null,
          },
          200,
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          {
            query: 'Babbage',
            results: [{ id: 'r2', title: 'Analytical Engine papers', year: '1843', source: 'Science Museum' }],
            briefing: null,
          },
          200,
        ),
      );

    render(<HomePage />);

    searchFor('Ada Lovelace');
    expect(await screen.findByText('Ada Lovelace correspondence')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Source 1 — British Library/ })).toBeInTheDocument();
    // The record's own text is shown, not just a title and a link.
    expect(screen.getByText('Letters discussing the Analytical Engine.')).toBeInTheDocument();
    // Relevance is a band, not a misleading confidence percentage.
    expect(screen.getByText('High relevance')).toBeInTheDocument();
    expect(screen.queryByText(/Match strength/)).not.toBeInTheDocument();
    expect(String(fetchMock.mock.calls[0][0])).toContain('/api/search?query=Ada%20Lovelace');

    // A different query issues a different request and replaces the results.
    searchFor('Babbage');
    expect(await screen.findByText('Analytical Engine papers')).toBeInTheDocument();
    expect(String(fetchMock.mock.calls[1][0])).toContain('/api/search?query=Babbage');
    expect(screen.queryByText('Ada Lovelace correspondence')).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('renders the grounded briefing above the source list', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        {
          query: '1946',
          results: [
            {
              id: 'wiki:1946',
              title: '1946',
              source: 'Wikipedia',
              url: 'https://en.wikipedia.org/wiki/1946',
              score: 0.98,
              excerpt: '1946 (MCMXLVI) was a common year starting on Tuesday.',
              relevance: 'high',
            },
          ],
          briefing: {
            query: '1946',
            queryKind: 'year',
            method: 'extractive-quotes',
            summary: 'Recorded events include: January 6 — The first general election ever in Vietnam is held.',
            explanation: '1946 (MCMXLVI) was a common year starting on Tuesday of the Gregorian calendar.',
            keyFacts: [],
            timeline: [
              {
                date: 'January 6',
                description: 'The first general election ever in Vietnam is held.',
                sourceIndex: 1,
              },
            ],
            people: ['January 1 (born): Alfonso Caruana'],
            related: ['1946 in film'],
            sources: [
              {
                index: 1,
                id: 'wiki:1946',
                title: '1946',
                url: 'https://en.wikipedia.org/wiki/1946',
                publisher: 'Wikipedia',
                relevance: 0.98,
              },
            ],
            uncertainty: [
              'The explanation quotes retrieved source material verbatim; it is a summary of secondary sources, not independent historical verification.',
            ],
          },
        },
        200,
      ),
    );

    render(<HomePage />);
    searchFor('1946');

    expect(await screen.findByText(/Recorded events include/)).toBeInTheDocument();
    // Dated events, people and the limitations are all visible.
    expect(screen.getByText('Dated events')).toBeInTheDocument();
    // The event appears both in the summary and as a dated timeline entry.
    expect(
      screen.getAllByText(/The first general election ever in Vietnam is held/).length,
    ).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('January 6')).toBeInTheDocument();
    expect(screen.getByText('People recorded')).toBeInTheDocument();
    expect(screen.getByText('January 1 (born): Alfonso Caruana')).toBeInTheDocument();
    expect(screen.getByText('Evidence and limitations')).toBeInTheDocument();
    // Provenance of the wording is stated, not implied.
    expect(screen.getByText(/Quoted from the retrieved sources/)).toBeInTheDocument();
    // …and the source itself is still one click away.
    expect(screen.getByRole('link', { name: /Source 1 — Wikipedia/ })).toHaveAttribute(
      'href',
      'https://en.wikipedia.org/wiki/1946',
    );
  });

  it('still shows the sources when the backend sends no briefing', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        {
          query: 'zeta',
          results: [{ id: 'r9', title: 'Zeta archive', source: 'Archive', url: 'https://example.org/z' }],
          briefing: null,
        },
        200,
      ),
    );

    render(<HomePage />);
    searchFor('zeta');

    expect(await screen.findByText('Zeta archive')).toBeInTheDocument();
    expect(screen.queryByText(/Quoted from the retrieved sources/)).not.toBeInTheDocument();
  });

  it('shows a neutral empty state — not an error — when nothing matches', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ query: 'zzz', results: [] }, 200));

    render(<HomePage />);
    searchFor('zzz nothing');

    const empty = await screen.findByText(/No relevant historical records were found/);
    // The empty state is neutral — and it says *why* nothing is shown.
    expect(empty.closest('[role="status"]')).not.toBeNull();
    expect(screen.getByText(/no sufficiently relevant source/)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('rejects invalid input in the browser without any request', async () => {
    render(<HomePage />);

    searchFor('');
    expect(await screen.findByRole('alert')).toHaveTextContent('Enter at least 2 characters');

    searchFor('A');
    expect(await screen.findByRole('alert')).toHaveTextContent('Enter at least 2 characters');

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('shows an error — distinguishable from empty results — when the network fails', async () => {
    fetchMock.mockRejectedValue(new TypeError('network down'));

    render(<HomePage />);
    searchFor('Ada Lovelace');

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Search failed.');
    expect(alert).toHaveTextContent('Could not reach the server');
    // Actionable, not a generic "check your connection": it names where the
    // API was expected and the thing the operator can act on.
    expect(alert).toHaveTextContent('Check that the backend is running');
    expect(screen.queryByText(/No relevant historical records/)).not.toBeInTheDocument();
  });

  it('reports a timeout instead of staying stuck when the server never answers', async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (String(url).includes('/api/search')) {
        return new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => {
            reject(new DOMException('Aborted', 'AbortError'));
          });
        });
      }
      return Promise.resolve(jsonResponse({ error: 'unauthorized' }, 401));
    });

    render(<HomePage />);
    searchFor('taj');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    vi.useRealTimers();

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('did not answer within 30 seconds');
    expect(screen.getByRole('button', { name: 'Search' })).toBeEnabled();
  });

  it('surfaces the backend message when the API answers with an error', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ error: 'search index offline' }, 503));

    render(<HomePage />);
    searchFor('Ada Lovelace');

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('search index offline');
    // An API failure never renders as an empty result.
    expect(screen.queryByText(/No relevant historical records/)).not.toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('recovers from a transient provider failure without a second search', async () => {
    // 429 is what the live source answers when it rate-limits; it is
    // transient, so the client retries it instead of asking the user to
    // press Search again — the "first attempt fails, repeating works" bug.
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ error: 'rate limited' }, 429))
      .mockResolvedValueOnce(
        jsonResponse(
          {
            query: 'Taj Mahal',
            results: [
              {
                id: 'wiki:799638',
                title: 'Taj Mahal',
                source: 'Wikipedia',
                url: 'https://en.wikipedia.org/wiki/Taj_Mahal',
              },
            ],
            briefing: null,
          },
          200,
        ),
      );

    render(<HomePage />);
    searchFor('Taj Mahal');

    expect(
      await screen.findByRole('heading', { name: 'Taj Mahal' }, { timeout: 4_000 }),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    // The retry is internal: the user never sees a failure they must fix.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('does not retry a permanent rejection', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ error: 'bad request' }, 400));

    render(<HomePage />);
    searchFor('Ada Lovelace');

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('bad request');
    // 400 would fail identically forever: retrying only delays the answer.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});