import { useImageFallback } from '../../hooks/useImageFallback';
import type { SubjectImage as SubjectImageData } from '../../types/api';

/**
 * R6 — the subject's photograph, with its provenance.
 *
 * Rules this component enforces:
 *
 * * the image is only ever a URL the backend returned for *this* subject;
 * * the same-origin proxy is tried before the original upstream URL, so a
 *   browser that cannot reach the image host still shows the photograph
 *   (and vice versa — see `useImageFallback`);
 * * the frame has a fixed aspect ratio, so an image loading never shifts the
 *   layout around it;
 * * when every source fails, the placeholder replaces the image, so the page
 *   never shows a broken-image icon;
 * * attribution, license and the file's description page are shown when the
 *   source supplied them — and nothing is claimed when it did not.
 */

export interface SubjectImageProps {
  image?: SubjectImageData | null;
  /** Alt text override; defaults to the backend's source-derived label. */
  alt?: string;
  /** Tailwind aspect class for the frame. */
  aspectClassName?: string;
  className?: string;
}

function Placeholder() {
  return (
    <div className="flex h-full w-full flex-col items-center justify-center gap-1 bg-ivory-100 text-center">
      <svg
        aria-hidden="true"
        viewBox="0 0 24 24"
        className="h-8 w-8 text-ivory-500"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
      >
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <circle cx="8.5" cy="9.5" r="1.5" />
        <path d="M4 17l5-5 3 3 3-3 5 5" />
      </svg>
      <p className="px-3 text-xs text-ink-500">
        No photograph available from the retrieved sources.
      </p>
    </div>
  );
}

export function SubjectImage({
  image,
  alt,
  aspectClassName = 'aspect-[4/3]',
  className,
}: SubjectImageProps) {
  const { src, handleError } = useImageFallback([image?.proxyUrl, image?.url]);

  // No renderable source (no image, or every source failed) → placeholder.
  // The `!image` half also narrows the type for everything below.
  if (!src || !image) {
    return (
      <figure
        className={`${aspectClassName} w-full overflow-hidden rounded-xl border border-ivory-300 ${className ?? ''}`}
        aria-label="Subject photograph unavailable"
      >
        <Placeholder />
      </figure>
    );
  }

  const creditParts = [image.credit, image.license].filter(
    (part): part is string => Boolean(part && part.trim()),
  );

  return (
    <figure className={className}>
      <div
        className={`${aspectClassName} w-full overflow-hidden rounded-xl border border-ivory-300 bg-ivory-100`}
      >
        <img
          src={src}
          alt={alt ?? image.altText ?? 'Photograph of the subject'}
          loading="lazy"
          decoding="async"
          onError={handleError}
          className="h-full w-full object-cover"
        />
      </div>

      {image.caption ? (
        <figcaption className="mt-2 text-xs leading-relaxed text-ink-500">
          {image.caption}
        </figcaption>
      ) : null}

      {creditParts.length > 0 || image.filePageUrl || image.sourcePageUrl ? (
        <p className="mt-1 text-xs text-ink-500">
          {creditParts.length > 0 ? (
            <>
              Image:{' '}
              {image.credit ? <span className="text-ink-700">{image.credit}</span> : null}
              {image.credit && image.license ? ' · ' : null}
              {image.license ? (
                image.licenseUrl ? (
                  <a
                    href={image.licenseUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-teal-700 underline underline-offset-2 hover:text-teal-800"
                  >
                    {image.license}
                  </a>
                ) : (
                  <span>{image.license}</span>
                )
              ) : null}
            </>
          ) : (
            'Image source'
          )}
          {image.filePageUrl ? (
            <>
              {creditParts.length > 0 || image.sourcePageUrl ? ' · ' : ''}
              <a
                href={image.filePageUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-teal-700 underline underline-offset-2 hover:text-teal-800"
              >
                File page
              </a>
            </>
          ) : null}
          {image.sourcePageUrl ? (
            <>
              {creditParts.length > 0 || image.filePageUrl ? ' · ' : ''}
              <a
                href={image.sourcePageUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-teal-700 underline underline-offset-2 hover:text-teal-800"
              >
                Source page
              </a>
            </>
          ) : null}
        </p>
      ) : null}
    </figure>
  );
}

export default SubjectImage;
