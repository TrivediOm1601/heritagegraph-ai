import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

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

const MODI_THUMB = 'https://upload.wikimedia.org/modi.jpg';
const LALIT_THUMB = 'https://upload.wikimedia.org/lalit.jpg';

/**
 * Images render through the same-origin proxy first (B5), with the upstream
 * URL as the fallback: that is what makes them survive a browser that cannot
 * reach the image host, and it is what these tests pin.
 */
function proxied(url: string): string {
  return `/api/media?url=${encodeURIComponent(url)}`;
}

function modiResponse() {
  return {
    query: 'Narendra Modi',
    results: [
      {
        id: 'wiki:444222',
        title: 'Narendra Modi',
        source: 'Wikipedia',
        url: 'https://en.wikipedia.org/wiki/Narendra_Modi',
        score: 0.95,
        excerpt: 'Narendra Modi is an Indian politician.',
        relevance: 'high',
      },
    ],
    briefing: {
      query: 'Narendra Modi',
      queryKind: 'named',
      method: 'extractive-quotes',
      summary: 'Narendra Modi is an Indian politician who has served as the Prime Minister of India since 2014.',
      explanation:
        'Narendra Modi is an Indian politician who has served as the Prime Minister of India since 2014. He was the Chief Minister of Gujarat from 2001 to 2014.',
      keyFacts: [
        {
          text: 'Narendra Modi is an Indian politician who has served as the Prime Minister of India since 2014.',
          sourceIndex: 1,
        },
      ],
      timeline: [],
      people: [],
      related: ['Lalit Modi'],
      relatedTopics: [
        {
          id: 'wiki:15938793',
          name: 'Lalit Modi',
          type: 'person',
          description: 'Indian cricket administrator and businessman',
          query: 'Lalit Modi',
          sourceUrl: 'https://en.wikipedia.org/wiki/Lalit_Modi',
          relevance: 0.7,
          image: {
            url: LALIT_THUMB,
            altText: 'Lalit Modi: Indian cricket administrator and businessman',
            license: 'CC BY 3.0',
            credit: 'topnews.in',
            filePageUrl: 'https://commons.wikimedia.org/wiki/File:Modi_at_IPL_Players_Auction.jpg',
          },
        },
      ],
      subject: {
        name: 'Narendra Modi',
        type: 'person',
        description: 'Prime Minister of India since 2014',
        sourceUrl: 'https://en.wikipedia.org/wiki/Narendra_Modi',
        image: {
          url: MODI_THUMB,
          altText: 'Narendra Modi: Prime Minister of India since 2014',
          credit: "Prime Minister's Office",
          license: 'GODL-India',
          licenseUrl: 'https://commons.wikimedia.org/wiki/Template:GODL-India',
          filePageUrl: 'https://commons.wikimedia.org/wiki/File:Narendra_Modi_Portrait_2026.jpg',
        },
      },
      sources: [
        {
          index: 1,
          id: 'wiki:444222',
          title: 'Narendra Modi',
          url: 'https://en.wikipedia.org/wiki/Narendra_Modi',
          publisher: 'Wikipedia',
          excerpt: 'Narendra Modi is an Indian politician.',
          relevance: 0.95,
        },
      ],
      uncertainty: [
        'The explanation quotes retrieved source material verbatim; it is a summary of secondary sources, not independent historical verification.',
      ],
    },
  };
}

function lalitResponse() {
  return {
    query: 'Lalit Modi',
    results: [
      {
        id: 'wiki:15938793',
        title: 'Lalit Modi',
        source: 'Wikipedia',
        url: 'https://en.wikipedia.org/wiki/Lalit_Modi',
        score: 0.9,
        excerpt: 'Lalit Modi is an Indian cricket administrator.',
        relevance: 'high',
      },
    ],
    briefing: {
      query: 'Lalit Modi',
      queryKind: 'named',
      method: 'extractive-quotes',
      summary: 'Lalit Modi is an Indian cricket administrator and businessman.',
      explanation: 'Lalit Modi is an Indian cricket administrator and businessman.',
      keyFacts: [],
      timeline: [],
      people: [],
      related: ['Narendra Modi'],
      relatedTopics: [],
      subject: {
        name: 'Lalit Modi',
        type: 'person',
        description: 'Indian cricket administrator and businessman',
        sourceUrl: 'https://en.wikipedia.org/wiki/Lalit_Modi',
        image: {
          url: LALIT_THUMB,
          altText: 'Lalit Modi: Indian cricket administrator and businessman',
          license: 'CC BY 3.0',
          credit: 'topnews.in',
          filePageUrl: 'https://commons.wikimedia.org/wiki/File:Modi_at_IPL_Players_Auction.jpg',
        },
      },
      sources: [
        {
          index: 1,
          id: 'wiki:15938793',
          title: 'Lalit Modi',
          url: 'https://en.wikipedia.org/wiki/Lalit_Modi',
          publisher: 'Wikipedia',
        },
      ],
      uncertainty: [],
    },
  };
}

describe('subject overview', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockReset();
    window.history.replaceState({}, '', '/');
  });

  it('discloses a degraded live source instead of implying an empty archive', async () => {
    const body = modiResponse();
    (body as unknown as Record<string, unknown>).notice =
      'The live historical source could not be reached or rate-limited this request, so these results may be incomplete. Please try again in a moment.';
    fetchMock.mockResolvedValue(jsonResponse(body, 200));

    render(<HomePage />);
    searchFor('Narendra Modi');

    const notice = await screen.findByTestId('search-notice');
    expect(notice).toHaveTextContent('rate-limited this request');
    // The subject is still shown — a degraded answer is not a failure.
    expect(screen.getByRole('heading', { name: 'Narendra Modi' })).toBeInTheDocument();
  });

  it('shows the subject photograph, description, type and attribution', async () => {
    fetchMock.mockResolvedValue(jsonResponse(modiResponse(), 200));

    render(<HomePage />);
    searchFor('Narendra Modi');

    const image = await screen.findByRole('img', { name: /Narendra Modi/ });
    expect(image).toHaveAttribute('src', proxied(MODI_THUMB));
    expect(screen.getByText('Prime Minister of India since 2014')).toBeInTheDocument();
    // The type chip belongs to the subject header (the related topic below
    // has its own).
    const header = screen.getByRole('banner');
    expect(within(header).getByText('Person')).toBeInTheDocument();
    expect(within(header).getByText('Subject overview')).toBeInTheDocument();
    // Attribution and license travel with the picture.
    expect(screen.getByRole('link', { name: 'GODL-India' })).toHaveAttribute(
      'href',
      'https://commons.wikimedia.org/wiki/Template:GODL-India',
    );
    expect(screen.getByRole('link', { name: 'File page' })).toHaveAttribute(
      'href',
      'https://commons.wikimedia.org/wiki/File:Narendra_Modi_Portrait_2026.jpg',
    );
    expect(screen.getByText(/Prime Minister's Office/)).toBeInTheDocument();
  });

  it('renders a placeholder when the subject has no photograph', async () => {
    const body = modiResponse();
    (body.briefing.subject as unknown as Record<string, unknown>).image = null;
    fetchMock.mockResolvedValue(jsonResponse(body, 200));

    render(<HomePage />);
    searchFor('Narendra Modi');

    expect(
      await screen.findByText('No photograph available from the retrieved sources.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('img', { name: /Narendra Modi/ })).not.toBeInTheDocument();
  });

  it('falls back to the original image URL when the proxy fails', async () => {
    fetchMock.mockResolvedValue(jsonResponse(modiResponse(), 200));

    render(<HomePage />);
    searchFor('Narendra Modi');

    const image = await screen.findByRole('img', { name: /Narendra Modi/ });
    expect(image).toHaveAttribute('src', proxied(MODI_THUMB));

    // A deployment without the backend proxy (or a failed proxy request)
    // must still show the photograph.
    fireEvent.error(image);

    const fallback = await screen.findByRole('img', { name: /Narendra Modi/ });
    expect(fallback).toHaveAttribute('src', MODI_THUMB);
  });

  it('shows the placeholder only after every image source fails', async () => {
    fetchMock.mockResolvedValue(jsonResponse(modiResponse(), 200));

    render(<HomePage />);
    searchFor('Narendra Modi');

    fireEvent.error(await screen.findByRole('img', { name: /Narendra Modi/ }));
    fireEvent.error(await screen.findByRole('img', { name: /Narendra Modi/ }));

    expect(
      await screen.findByText('No photograph available from the retrieved sources.'),
    ).toBeInTheDocument();
  });

  it('does not repeat the summary paragraph inside the explanation', async () => {
    fetchMock.mockResolvedValue(jsonResponse(modiResponse(), 200));

    render(<HomePage />);
    searchFor('Narendra Modi');

    // The opening sentence appears once as the summary and once as the
    // first sentence of the explanation; the duplicate is pruned.
    const shared = await screen.findAllByText(
      /Narendra Modi is an Indian politician who has served as the Prime Minister of India since 2014\./,
    );
    expect(shared.filter((node) => node.tagName === 'P')).toHaveLength(1);
    expect(screen.getByText(/He was the Chief Minister of Gujarat/)).toBeInTheDocument();
  });

  it('labels a generated summary as generated, with its provider', async () => {
    const body = modiResponse();
    // The synthesis block is optional in the contract; add it for this case.
    (body.briefing as unknown as Record<string, unknown>).synthesis = {
      provider: 'Adaption',
      model: 'hosted-model-1',
      method: 'adaption-grounded-synthesis',
      text: 'Narendra Modi has been Prime Minister of India since 2014 [1].',
      sourceIndexes: [1],
    };
    fetchMock.mockResolvedValue(jsonResponse(body, 200));

    render(<HomePage />);
    searchFor('Narendra Modi');

    const block = await screen.findByTestId('briefing-synthesis');
    expect(block).toHaveTextContent('AI summary of the cited sources');
    expect(block).toHaveTextContent('Adaption');
    expect(block).toHaveTextContent('Generated text — not a quotation');
  });
});

describe('related topic navigation', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockReset();
    window.history.replaceState({}, '', '/');
  });

  it('opens the clicked topic, replaces the content and records it in the URL', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(modiResponse(), 200))
      .mockResolvedValueOnce(jsonResponse(lalitResponse(), 200));

    render(<HomePage />);
    searchFor('Narendra Modi');

    const topic = await screen.findByTestId('related-topic');
    expect(topic).toHaveTextContent('Lalit Modi');
    expect(topic).toHaveTextContent('Indian cricket administrator and businessman');

    fireEvent.click(topic);

    // The topic's own overview is loaded — not Narendra Modi's under a new
    // heading.
    expect(
      await screen.findByRole('heading', { name: 'Lalit Modi' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('heading', { name: 'Narendra Modi' }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('img', { name: /Lalit Modi/ })).toHaveAttribute(
      'src',
      proxied(LALIT_THUMB),
    );
    expect(String(fetchMock.mock.calls[1][0])).toContain('/api/search?query=Lalit%20Modi');
    expect(window.location.search).toBe('?q=Lalit+Modi');
  });

  it('returns to the previous subject on Back, and forward again', async () => {
    // Answer by the subject in the URL, so the assertions below can only be
    // satisfied if the *right* subject was requested.
    fetchMock.mockImplementation((url: string) =>
      Promise.resolve(
        jsonResponse(
          String(url).includes('Lalit') ? lalitResponse() : modiResponse(),
          200,
        ),
      ),
    );

    render(<HomePage />);
    searchFor('Narendra Modi');
    await screen.findByRole('heading', { name: 'Narendra Modi' });
    expect(window.location.search).toBe('?q=Narendra+Modi');

    fireEvent.click(await screen.findByTestId('related-topic'));
    await screen.findByRole('heading', { name: 'Lalit Modi' });
    expect(window.location.search).toBe('?q=Lalit+Modi');

    // Back: the browser restores the previous entry's URL and fires popstate.
    window.history.replaceState({}, '', '/?q=Narendra%20Modi');
    window.dispatchEvent(new PopStateEvent('popstate'));
    expect(
      await screen.findByRole('heading', { name: 'Narendra Modi' }),
    ).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Lalit Modi' })).not.toBeInTheDocument();

    // Forward: the topic's URL comes back and so does its overview.
    window.history.replaceState({}, '', '/?q=Lalit%20Modi');
    window.dispatchEvent(new PopStateEvent('popstate'));
    expect(await screen.findByRole('heading', { name: 'Lalit Modi' })).toBeInTheDocument();
  });

  it('returns to the prompt when the URL has no subject', async () => {
    fetchMock.mockResolvedValue(jsonResponse(modiResponse(), 200));

    render(<HomePage />);
    searchFor('Narendra Modi');
    await screen.findByRole('heading', { name: 'Narendra Modi' });

    window.history.replaceState({}, '', '/');
    window.dispatchEvent(new PopStateEvent('popstate'));

    await waitFor(() => {
      expect(screen.queryByTestId('subject-overview')).not.toBeInTheDocument();
    });
  });

  it('opens a shared subject link directly', async () => {
    fetchMock.mockResolvedValue(jsonResponse(modiResponse(), 200));
    // A topic link pasted into the address bar is the same as clicking it.
    window.history.replaceState({}, '', '/?q=Narendra%20Modi');

    render(<HomePage />);

    expect(
      await screen.findByRole('heading', { name: 'Narendra Modi' }),
    ).toBeInTheDocument();
    expect(String(fetchMock.mock.calls[0][0])).toContain('/api/search?query=Narendra%20Modi');
  });

  it('intercepts the click instead of reloading the document', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(modiResponse(), 200))
      .mockResolvedValueOnce(jsonResponse(lalitResponse(), 200));
    const pushState = vi.spyOn(window.history, 'pushState');

    render(<HomePage />);
    searchFor('Narendra Modi');
    fireEvent.click(await screen.findByTestId('related-topic'));
    await screen.findByRole('heading', { name: 'Lalit Modi' });

    // One history entry per subject — opened in place, not via a reload.
    expect(pushState).toHaveBeenCalledTimes(2);
    pushState.mockRestore();
  });

  it('exposes topics as real links for keyboard and new-tab use', async () => {
    fetchMock.mockResolvedValue(jsonResponse(modiResponse(), 200));

    render(<HomePage />);
    searchFor('Narendra Modi');

    const link = await screen.findByRole('link', { name: /Lalit Modi/ });
    expect(link).toHaveAttribute('href', '?q=Lalit%20Modi');
  });
});
