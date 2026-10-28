import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

import { SearchGraphPanel } from '../src/components/search/SearchGraphPanel';

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

const fetchMock = vi.fn();

const GRAPH = {
  query: 'ada lovelace',
  nodes: [
    { id: 'subject:ada lovelace', label: 'Ada Lovelace', type: 'person' },
    { id: 'rec-1', label: 'Ada Lovelace', type: 'record' },
    { id: 'rec-2', label: 'Charles Babbage', type: 'record' },
  ],
  edges: [
    {
      id: 'retrieved:rec-1',
      source: 'subject:ada lovelace',
      target: 'rec-1',
      label: 'retrieved for',
      verificationStatus: 'unverified',
      evidence: [
        { source: 'Test archive', excerpt: 'Mathematician and writer.', url: 'https://example.org/a' },
      ],
    },
    {
      id: 'comention:rec-2->rec-1',
      source: 'rec-2',
      target: 'rec-1',
      label: 'mentions Ada Lovelace',
      verificationStatus: 'unverified',
      evidence: [{ source: 'Test archive', excerpt: 'He worked with Ada Lovelace.' }],
    },
  ],
  sources: [{ source: 'Test archive', excerpt: 'Mathematician and writer.', url: 'https://example.org/a' }],
  limitations: ['No retrieved record names another one in its own text.'],
  notice: null,
};

describe('SearchGraphPanel', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockReset();
  });

  it('renders the graph for the searched subject with its limitations', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(GRAPH));

    render(<SearchGraphPanel query="Ada Lovelace" />);

    expect(await screen.findByText(/Edges are drawn only/)).toBeInTheDocument();
    expect(screen.getByLabelText('Knowledge graph')).toBeInTheDocument();
    expect(screen.getByText(/No retrieved record names another one/)).toBeInTheDocument();

    const requested = String(fetchMock.mock.calls[0][0]);
    expect(requested).toContain('/api/search/graph?query=Ada%20Lovelace');
  });

  it('shows an honest empty state when no graph can be built', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        query: 'zzz',
        nodes: [],
        edges: [],
        sources: [],
        limitations: ['No historical records were retrieved for this query, so no graph can be built.'],
      }),
    );

    render(<SearchGraphPanel query="zzz" />);

    expect(await screen.findByText('No graph for this search')).toBeInTheDocument();
    expect(screen.getByText(/No historical records were retrieved/)).toBeInTheDocument();
    expect(screen.queryByLabelText('Knowledge graph')).not.toBeInTheDocument();
  });

  it('reports a failed graph request instead of rendering a fake graph', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: 'dependency_unavailable', message: 'Search is unavailable.' } }, 503),
    );

    render(<SearchGraphPanel query="Ada Lovelace" />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Search is unavailable.');
    expect(screen.queryByLabelText('Knowledge graph')).not.toBeInTheDocument();
  });

  it('does not fetch anything when the query changes and the panel unmounts', async () => {
    fetchMock.mockResolvedValue(jsonResponse(GRAPH));
    const { unmount } = render(<SearchGraphPanel query="Ada Lovelace" />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    unmount();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
