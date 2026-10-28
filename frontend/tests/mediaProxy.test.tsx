import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { imageSourceChain, useImageFallback } from '../src/hooks/useImageFallback';

const UPLOAD = 'https://upload.wikimedia.org/wikipedia/commons/a/a5/photo.jpg';
const proxied = (url: string) => `/api/media?url=${encodeURIComponent(url)}`;

function Probe({ sources }: { sources: Array<string | null | undefined> }) {
  const { src, failed, handleError } = useImageFallback(sources);
  return (
    <div>
      <span data-testid="src">{src ?? 'none'}</span>
      <span data-testid="failed">{String(failed)}</span>
      <button type="button" onClick={handleError}>
        fail
      </button>
    </div>
  );
}

describe('image source chain', () => {
  it('keeps order, trims and de-duplicates', () => {
    expect(
      imageSourceChain(proxied(UPLOAD), ` ${UPLOAD} `, null, undefined, '', proxied(UPLOAD)),
    ).toEqual([proxied(UPLOAD), UPLOAD]);
  });

  it('is empty when nothing usable was supplied', () => {
    expect(imageSourceChain(null, undefined, '   ')).toEqual([]);
  });
});

describe('useImageFallback', () => {
  it('tries the same-origin proxy first and falls back to the original URL', () => {
    render(<Probe sources={[proxied(UPLOAD), UPLOAD]} />);

    expect(screen.getByTestId('src')).toHaveTextContent(proxied(UPLOAD));
    expect(screen.getByTestId('failed')).toHaveTextContent('false');

    fireEvent.click(screen.getByRole('button', { name: 'fail' }));

    expect(screen.getByTestId('src')).toHaveTextContent(UPLOAD);
    expect(screen.getByTestId('failed')).toHaveTextContent('false');
  });

  it('reports failure only once the whole chain is exhausted', () => {
    render(<Probe sources={[proxied(UPLOAD), UPLOAD]} />);

    fireEvent.click(screen.getByRole('button', { name: 'fail' }));
    fireEvent.click(screen.getByRole('button', { name: 'fail' }));

    expect(screen.getByTestId('src')).toHaveTextContent('none');
    expect(screen.getByTestId('failed')).toHaveTextContent('true');
  });

  it('reports failure straight away when there is no source at all', () => {
    render(<Probe sources={[null, undefined]} />);

    expect(screen.getByTestId('src')).toHaveTextContent('none');
    expect(screen.getByTestId('failed')).toHaveTextContent('true');
  });

  it('starts a new subject image from the first source again', () => {
    const { rerender } = render(<Probe sources={[proxied(UPLOAD), UPLOAD]} />);
    fireEvent.click(screen.getByRole('button', { name: 'fail' }));
    expect(screen.getByTestId('src')).toHaveTextContent(UPLOAD);

    const other = 'https://upload.wikimedia.org/wikipedia/commons/b/b1/other.jpg';
    rerender(<Probe sources={[proxied(other), other]} />);

    expect(screen.getByTestId('src')).toHaveTextContent(proxied(other));
  });
});

describe('mediaProxyUrl / resolveApiBaseUrl', () => {
  it('proxies absolute image URLs on the page origin by default', async () => {
    const { mediaProxyUrl, API_BASE_URL } = await import('../src/services/api');

    // Same-origin by default: no cross-origin request, no hardcoded host.
    expect(API_BASE_URL).toBe('');
    expect(mediaProxyUrl(UPLOAD)).toBe(proxied(UPLOAD));
  });

  it('leaves non-absolute URLs alone', async () => {
    const { mediaProxyUrl } = await import('../src/services/api');

    expect(mediaProxyUrl('/local/image.png')).toBeNull();
    expect(mediaProxyUrl('data:image/png;base64,AAAA')).toBeNull();
    expect(mediaProxyUrl(null)).toBeNull();
    expect(mediaProxyUrl('   ')).toBeNull();
  });

  it('accepts an absolute API base URL and strips a trailing slash', async () => {
    vi.resetModules();
    vi.stubEnv('VITE_API_BASE_URL', 'http://127.0.0.1:8000/');
    const { API_BASE_URL, mediaProxyUrl } = await import('../src/services/api');

    expect(API_BASE_URL).toBe('http://127.0.0.1:8000');
    expect(mediaProxyUrl(UPLOAD)).toBe(`http://127.0.0.1:8000${proxied(UPLOAD)}`);

    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it('rejects a malformed API base URL loudly and stays same-origin', async () => {
    vi.resetModules();
    vi.stubEnv('VITE_API_BASE_URL', 'localhost:8000');
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});

    const { API_BASE_URL } = await import('../src/services/api');

    expect(API_BASE_URL).toBe('');
    expect(spy).toHaveBeenCalledWith(expect.stringContaining('VITE_API_BASE_URL'));

    spy.mockRestore();
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it('can be turned off with VITE_MEDIA_PROXY=off', async () => {
    vi.resetModules();
    vi.stubEnv('VITE_MEDIA_PROXY', 'off');

    const { mediaProxyUrl } = await import('../src/services/api');

    expect(mediaProxyUrl(UPLOAD)).toBeNull();

    vi.unstubAllEnvs();
    vi.resetModules();
  });
});
