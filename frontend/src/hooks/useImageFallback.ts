import { useEffect, useMemo, useState } from 'react';

/**
 * B5 — image sources with a fallback chain.
 *
 * A subject photograph has two possible sources: the same-origin proxy
 * (`/api/media?url=…`, which works even when the browser cannot reach the
 * image host) and the original upstream URL. Whichever comes first is tried
 * first; if it fails to load, the next one is tried; only when the chain is
 * exhausted does the UI show its placeholder.
 *
 * That order matters because the two failure modes are opposite: a browser
 * with shields/DNS filtering can reach the app but not the image host, and a
 * deployment without the backend proxy serves the original URL fine. Trying
 * both means neither case shows a broken image.
 */

/** Trimmed, de-duplicated, non-empty sources in priority order. */
export function imageSourceChain(...candidates: Array<string | null | undefined>): string[] {
  const chain: string[] = [];
  for (const candidate of candidates) {
    const url = (candidate ?? '').trim();
    if (url && !chain.includes(url)) chain.push(url);
  }
  return chain;
}

export interface ImageFallback {
  /** The source to render, or null when none is left (show the placeholder). */
  src: string | null;
  /** True once every source has failed (or none was given). */
  failed: boolean;
  /** Advance to the next source; wire this to the image's onError. */
  handleError: () => void;
}

export function useImageFallback(
  candidates: Array<string | null | undefined>,
): ImageFallback {
  const key = candidates.map((candidate) => (candidate ?? '').trim()).join('\u0000');
  // `key` is the stable identity of the candidate list.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const chain = useMemo(() => imageSourceChain(...candidates), [key]);
  const [index, setIndex] = useState(0);

  // A new image (or a re-fetched subject) starts from the first source again.
  useEffect(() => setIndex(0), [key]);

  const src = index < chain.length ? chain[index] : null;

  return {
    src,
    failed: src === null,
    handleError: () => setIndex((current) => current + 1),
  };
}

export default useImageFallback;
