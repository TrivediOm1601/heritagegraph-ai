/**
 * Integration-audit tests: error-envelope parsing (INT-007).
 *
 * The backend's agreed error format is
 *   { "error": { "code", "message", "request_id" } }
 * and the UI must surface `error.message` as plain text — never the raw
 * JSON blob. Legacy shapes ({ error: string }, { message: string },
 * plain-text bodies) must keep working as fallbacks.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  ApiError,
  getAnalysisGraph,
  searchHistoricalRecords,
  submitAnalysis,
} from '../src/services/api';

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

/** Await a promise that must reject with an ApiError. */
async function expectApiError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (caught) {
    expect(caught).toBeInstanceOf(ApiError);
    return caught as ApiError;
  }
  throw new Error('expected the call to reject with an ApiError');
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('canonical error envelope', () => {
  it('surfaces error.message from the canonical envelope (search)', async () => {
    const message =
      'Search is unavailable: the historical record store could not be reached.';
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            error: {
              code: 'dependency_unavailable',
              message,
              request_id: 'req-1',
            },
          },
          503,
        ),
      ),
    );

    const error = await expectApiError(searchHistoricalRecords('Ada Lovelace'));

    expect(error.message).toBe(message);
    expect(error.status).toBe(503);
    expect(error.message.startsWith('{')).toBe(false); // never raw JSON
  });

  it('surfaces error.message for uploads (413) with its status', async () => {
    const message = 'The uploaded image exceeds the 10 MB limit.';
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse(
          { error: { code: 'payload_too_large', message, request_id: 'req-2' } },
          413,
        ),
      ),
    );

    const file = new File(['x'], 'photo.png', { type: 'image/png' });
    const error = await expectApiError(submitAnalysis(file));

    expect(error.message).toBe(message);
    expect(error.status).toBe(413);
  });

  it('surfaces error.message for the graph endpoint', async () => {
    const message = 'The knowledge graph is unavailable: the database could not be reached.';
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse(
          { error: { code: 'dependency_unavailable', message, request_id: 'req-3' } },
          503,
        ),
      ),
    );

    const error = await expectApiError(getAnalysisGraph('an-1'));

    expect(error.message).toBe(message);
  });

  it('keeps the legacy string-error shape working', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ error: 'plain legacy failure' }, 500)),
    );

    await expect(searchHistoricalRecords('Ada Lovelace')).rejects.toThrow(
      'plain legacy failure',
    );
  });

  it('falls back to flat text for non-JSON bodies', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response('Something went wrong upstream', {
          status: 502,
          headers: { 'content-type': 'text/plain' },
        }),
      ),
    );

    const error = await expectApiError(searchHistoricalRecords('Ada Lovelace'));

    expect(error.message).toContain('Something went wrong upstream');
    expect(error.status).toBe(502);
  });
});
