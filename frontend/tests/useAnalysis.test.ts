import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';

import { useAnalysis } from '../src/hooks/useAnalysis';
import { getAnalysis } from '../src/services/api';
import type { SubmitAnalysisSuccess } from '../src/services/api';

/** Minimal Response stand-in: api.ts only reads ok/status/json/text. */
function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

function pngFile(name = 'scan.png'): File {
  return new File([new Uint8Array([137, 80, 78, 71])], name, { type: 'image/png' });
}

const fetchMock = vi.fn();

describe('useAnalysis', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockReset();
  });

  it('reaches success only after the backend accepts the upload', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ analysisId: 'job-1', status: 'queued' }, 201));

    const { result } = renderHook(() => useAnalysis());
    expect(result.current.phase).toBe('idle');

    let outcome: Awaited<ReturnType<typeof result.current.submitAnalysis>> = null;
    await act(async () => {
      outcome = await result.current.submitAnalysis(pngFile());
    });

    expect(outcome).toEqual({ analysisId: 'job-1', status: 'queued' });
    expect(result.current.phase).toBe('success');
    expect(result.current.submission).toEqual({ analysisId: 'job-1', status: 'queued' });
    expect(result.current.error).toBeNull();
  });

  it('reports an error, never success, when the backend rejects the upload', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ error: 'model unavailable' }, 503));

    const { result } = renderHook(() => useAnalysis());
    let outcome: Awaited<ReturnType<typeof result.current.submitAnalysis>> = null;
    await act(async () => {
      outcome = await result.current.submitAnalysis(pngFile());
    });

    expect(outcome).toBeNull();
    expect(result.current.phase).toBe('error');
    expect(result.current.error).toContain('model unavailable');
    expect(result.current.submission).toBeNull();
  });

  it('treats a 2xx response carrying an error payload as a failure', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ error: 'quota exceeded' }, 200));

    const { result } = renderHook(() => useAnalysis());
    await act(async () => {
      await result.current.submitAnalysis(pngFile());
    });

    expect(result.current.phase).toBe('error');
    expect(result.current.error).toContain('quota exceeded');
    expect(result.current.submission).toBeNull();
  });

  it('surfaces a transport failure as an error state', async () => {
    fetchMock.mockRejectedValue(new TypeError('network down'));

    const { result } = renderHook(() => useAnalysis());
    await act(async () => {
      await result.current.submitAnalysis(pngFile());
    });

    expect(result.current.phase).toBe('error');
    expect(result.current.error).toContain('Could not reach the server');
    expect(result.current.submission).toBeNull();
  });

  it('rejects an unsupported file without any network call', async () => {
    const { result } = renderHook(() => useAnalysis());

    let outcome: Awaited<ReturnType<typeof result.current.submitAnalysis>> = null;
    await act(async () => {
      outcome = await result.current.submitAnalysis(
        new File(['x'], 'doc.pdf', { type: 'application/pdf' }),
      );
    });

    expect(outcome).toBeNull();
    expect(result.current.phase).toBe('error');
    expect(result.current.error).toContain('not a supported image');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('accepts a partial run instead of turning it into a load error', async () => {
    // A run that finished with useful visual output but no extracted text and
    // no entities is a legitimate terminal state (`partial`). Treating it as an
    // unrecognised status replaced a real result with "could not load".
    fetchMock.mockResolvedValue(
      jsonResponse({
        id: 'job-partial',
        status: 'partial',
        interpretation: 'An open manuscript on a stand.',
        extractedText: null,
        entities: [],
        matches: [],
        briefing: null,
        warnings: ['No entities were detected in the image.'],
        error: null,
      }),
    );

    const analysis = await getAnalysis('job-partial');

    expect(analysis.status).toBe('partial');
    expect(analysis.warnings).toEqual(['No entities were detected in the image.']);
    expect(analysis.interpretation).toContain('manuscript');
  });

  it('ignores repeated submissions while one is in flight', async () => {
    const deferred: { resolve: ((response: Response) => void) | null } = { resolve: null };
    fetchMock.mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          deferred.resolve = resolve;
        }),
    );

    const { result } = renderHook(() => useAnalysis());

    let first: Promise<SubmitAnalysisSuccess | null> | null = null;
    await act(async () => {
      first = result.current.submitAnalysis(pngFile());
    });

    expect(result.current.phase).toBe('submitting');
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // A second call (double click, Enter key, etc.) must not fire a request.
    let second: Awaited<ReturnType<typeof result.current.submitAnalysis>> = null;
    await act(async () => {
      second = await result.current.submitAnalysis(pngFile('other.png'));
    });

    expect(second).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // Completing the first request still lands in exactly one success state.
    await act(async () => {
      deferred.resolve?.(jsonResponse({ analysisId: 'job-9', status: 'queued' }, 201));
      await first;
    });

    expect(result.current.phase).toBe('success');
    expect(result.current.submission).toEqual({ analysisId: 'job-9', status: 'queued' });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});