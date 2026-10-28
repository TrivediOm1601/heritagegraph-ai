import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { HistoryPage, formatWhen } from '../src/pages/HistoryPage';

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

function pdfResponse(body = '%PDF-1.7\nfake') {
  const bytes = new TextEncoder().encode(body);
  return {
    ok: true,
    status: 200,
    headers: { get: () => 'application/pdf' },
    blob: async () => new Blob([bytes], { type: 'application/pdf' }),
    json: async () => ({}),
    text: async () => body,
  } as unknown as Response;
}

const fetchMock = vi.fn();

const ITEMS = [
  {
    id: 'h1',
    kind: 'text_search',
    query: 'ada lovelace',
    status: 'completed',
    summary: '1 record matched “ada lovelace” — best match: Ada Lovelace.',
    createdAt: '2026-10-29T10:00:00+00:00',
    updatedAt: '2026-10-29T10:00:00+00:00',
  },
  {
    id: 'h2',
    kind: 'image_analysis',
    query: 'monument.png',
    analysisId: 'a1',
    status: 'partial',
    summary: 'Image analysis of monument.png.',
    createdAt: '2026-10-28T09:00:00+00:00',
    updatedAt: '2026-10-28T09:05:00+00:00',
  },
];

const DETAIL = {
  entry: ITEMS[0],
  analysis: null,
  graph: null,
  search: {
    query: 'ada lovelace',
    results: [
      { id: 'rec-1', title: 'Ada Lovelace', source: 'Test archive', url: 'https://example.org/a' },
    ],
    briefing: null,
    notice: null,
  },
  limitations: ['The stored search result could not be read; showing its summary only.'],
};

describe('HistoryPage', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockReset();
  });

  it('lists both activity types with their status', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ items: ITEMS, total: 2, limit: 20, offset: 0 }));

    render(<HistoryPage onOpenAnalysis={vi.fn()} onOpenSearch={vi.fn()} />);

    expect(await screen.findByText('ada lovelace')).toBeInTheDocument();
    expect(screen.getByText('monument.png')).toBeInTheDocument();
    expect(screen.getByText('Text search')).toBeInTheDocument();
    expect(screen.getByText('Image analysis')).toBeInTheDocument();
    // A partial run is labelled partial, not completed.
    expect(screen.getByText('Partial')).toBeInTheDocument();
  });

  it('shows a useful empty state', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ items: [], total: 0, limit: 20, offset: 0 }));

    render(<HistoryPage onOpenAnalysis={vi.fn()} onOpenSearch={vi.fn()} />);

    expect(await screen.findByText('Nothing here yet')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Export all as PDF/ })).toBeDisabled();
  });

  it('opens a text search record and replays its stored result', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ items: ITEMS, total: 2, limit: 20, offset: 0 }))
      .mockResolvedValueOnce(jsonResponse(DETAIL));

    render(<HistoryPage onOpenAnalysis={vi.fn()} onOpenSearch={vi.fn()} />);
    fireEvent.click(await screen.findByText('ada lovelace'));

    expect(await screen.findByText('Stored result')).toBeInTheDocument();
    expect(screen.getByText(/stored search result could not be read/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Source 1 — Test archive/ })).toBeInTheDocument();
    // No re-analysis request is made.
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('exports one record and only reports success for a real PDF', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ items: ITEMS, total: 2, limit: 20, offset: 0 }))
      .mockResolvedValueOnce(pdfResponse());

    render(<HistoryPage onOpenAnalysis={vi.fn()} onOpenSearch={vi.fn()} />);
    const buttons = await screen.findAllByRole('button', { name: 'Export PDF' });
    fireEvent.click(buttons[0]);

    expect(await screen.findByText('PDF downloaded.')).toBeInTheDocument();
    expect(String(fetchMock.mock.calls[1][0])).toContain('/api/history/h1/pdf');
  });

  it('does not claim success when the export fails', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ items: ITEMS, total: 2, limit: 20, offset: 0 }))
      .mockResolvedValueOnce(
        jsonResponse({ error: { code: 'not_found', message: 'No history record exists.' } }, 404),
      );

    render(<HistoryPage onOpenAnalysis={vi.fn()} onOpenSearch={vi.fn()} />);
    const buttons = await screen.findAllByRole('button', { name: 'Export PDF' });
    fireEvent.click(buttons[0]);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('No history record exists.');
    expect(screen.queryByText('PDF downloaded.')).not.toBeInTheDocument();
  });

  it('rejects a non-PDF download body', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ items: ITEMS, total: 2, limit: 20, offset: 0 }))
      .mockResolvedValueOnce(pdfResponse('{"not":"a pdf"}'));

    render(<HistoryPage onOpenAnalysis={vi.fn()} onOpenSearch={vi.fn()} />);
    const buttons = await screen.findAllByRole('button', { name: 'Export PDF' });
    fireEvent.click(buttons[0]);

    expect(await screen.findByRole('alert')).toHaveTextContent(/did not return a PDF/);
  });

  it('filters the list by activity type', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ items: [ITEMS[1]], total: 1, limit: 20, offset: 0 }));

    render(<HistoryPage onOpenAnalysis={vi.fn()} onOpenSearch={vi.fn()} />);
    await screen.findByText('monument.png');

    fireEvent.click(screen.getByRole('tab', { name: 'Image analyses' }));

    await waitFor(() =>
      expect(String(fetchMock.mock.calls.at(-1)?.[0])).toContain('kind=image_analysis'),
    );
  });
});

describe('formatWhen', () => {
  it('renders a readable timestamp and tolerates a missing value', () => {
    expect(formatWhen('2026-10-29T10:00:00+00:00')).not.toBe('Date not recorded');
    expect(formatWhen('')).toBe('Date not recorded');
    expect(formatWhen('not-a-date')).toBe('Date not recorded');
  });
});
