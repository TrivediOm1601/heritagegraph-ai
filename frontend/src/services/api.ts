/**
 * A2 — analysis upload service.
 *
 * CONTRACT NOTE (for Member 2): contract TYPES live in
 * frontend/src/types/api.ts (consolidated there in A5; still awaiting your
 * review/ownership). This module holds the transport: provisional endpoint
 * constants and the request/response parsing. Freezing the real contract is
 * therefore a small, local change: adjust types/api.ts and the normalisers
 * below. The hook and the components above this layer only depend on
 * `submitAnalysis`, `ApiError` and primitive props.
 *
 * Request format (PROVISIONAL, matches the plan's "agreed multipart request
 * format" once Member 2 confirms it):
 *
 *   POST {VITE_API_BASE_URL}/api/analysis
 *   Content-Type: multipart/form-data (set by the browser, with boundary)
 *   form field "file" = the image binary
 *   Accept: application/json
 *
 * Response envelope (PROVISIONAL):
 *   2xx  { "analysisId": string, "status"?: "queued" | "processing" }
 *   err  HTTP status + { "error" | "message": string }
 *
 * Safety rule enforced here: a success value is returned ONLY for a 2xx
 * response whose body matches the provisional envelope. Any other shape,
 * any error field, any transport failure rejects with ApiError, so the UI
 * can never report success when the backend reports an error.
 */

/**
 * Resolve the API base URL.
 *
 * Default: `""` — relative paths, i.e. the page's own origin. That is the
 * documented deployment (the dev server proxies `/api`; production serves
 * both from one origin) and it is what makes the app work in every browser:
 * there is no cross-origin request to be blocked, and no hardcoded
 * `localhost` that would mean a different machine for whoever opened the
 * page from anywhere but the development host.
 *
 * `VITE_API_BASE_URL` overrides it for standalone builds and integration
 * tests. A value that is not an absolute http(s) URL is reported loudly and
 * ignored: one clear console error beats every request silently failing
 * against a malformed origin.
 */
export function resolveApiBaseUrl(raw: unknown): string {
  const value = typeof raw === 'string' ? raw.trim() : '';
  if (!value) return '';
  if (!/^https?:\/\/[^/\s]+/i.test(value)) {
    console.error(
      '[heritagegraph] VITE_API_BASE_URL must be an absolute http(s) URL such as ' +
        `"http://127.0.0.1:8000" (got ${JSON.stringify(value)}); ignoring it and ` +
        'calling the API on this page\u2019s own origin (/api) instead.',
    );
    return '';
  }
  const normalised = value.replace(/\/+$/, '');
  // A cross-origin API base is legitimate for a standalone build, but in
  // development it silently turns every call into a CORS request — the exact
  // situation that shows up as "could not reach the server". Say so once.
  if (import.meta.env.DEV && typeof window !== 'undefined') {
    try {
      if (new URL(normalised).origin !== window.location.origin) {
        console.warn(
          `[heritagegraph] VITE_API_BASE_URL points at ${normalised}, which is a different ` +
            `origin from this page (${window.location.origin}). The backend must allow ` +
            `that origin via CORS_ORIGINS, or leave VITE_API_BASE_URL unset to use the ` +
            'same-origin /api proxy.',
        );
      }
    } catch {
      // Unreachable: the absolute-URL check above already validated the shape.
    }
  }
  return normalised;
}

/** Base URL for backend calls; "" = same-origin relative paths (default). */
export const API_BASE_URL: string = resolveApiBaseUrl(import.meta.env.VITE_API_BASE_URL);

// `formatBytes` moved to utils/formatters.ts in A3; re-exported here so
// existing imports from this module keep working.
export { formatBytes } from '../utils/formatters';
import { formatBytes } from '../utils/formatters';

// A5: contract types moved to types/api.ts (single source of truth).
// Re-exported so existing imports from this module keep working.
import type {
  AnalysisEntity,
  AnalysisResponse,
  AnalysisState,
  BriefingSource,
  BriefingSynthesis,
  EvidenceItem,
  EvidenceResponse,
  GraphEdge,
  GraphNode,
  GraphResponse,
  HistoricalBriefing,
  HistoricalMatch,
  KeyFact,
  RelatedTopic,
  SearchResponse,
  SubjectImage,
  SubjectProfile,
  SubmitAnalysisSuccess,
  TimelineEntry,
} from '../types/api';
export type * from '../types/api';

/** PROVISIONAL: Module B's analysis endpoint. */
export const ANALYSIS_ENDPOINT = '/api/analysis';

/** B5: same-origin proxy that serves upstream subject images. */
export const MEDIA_ENDPOINT = '/api/media';

/**
 * Set `VITE_MEDIA_PROXY=off` to load image URLs directly instead of through
 * the backend. On by default: same-origin images cannot be broken by a
 * browser's shields, DNS filtering or proxy, while a direct third-party
 * request can.
 */
const MEDIA_PROXY_ENABLED = !['0', 'off', 'false', 'no'].includes(
  String(import.meta.env.VITE_MEDIA_PROXY ?? 'on').trim().toLowerCase(),
);

/**
 * Same-origin proxy candidate for an absolute image URL, or null when one
 * cannot be built (relative/data URLs, or the proxy disabled). The caller
 * keeps the original URL as the next fallback, so a deployment whose
 * backend has no proxy still shows images.
 */
export function mediaProxyUrl(url?: string | null): string | null {
  const value = (url ?? '').trim();
  if (!value || !MEDIA_PROXY_ENABLED) return null;
  if (!/^https?:\/\//i.test(value)) return null;
  return `${API_BASE_URL}${MEDIA_ENDPOINT}?url=${encodeURIComponent(value)}`;
}

/** PROVISIONAL: multipart field name carrying the image payload. */
export const ANALYSIS_FILE_FIELD = 'file';

/** Image MIME types accepted by the browser-side guard. */
export const ACCEPTED_IMAGE_TYPES = [
  'image/jpeg',
  'image/png',
  'image/webp',
  'image/gif',
  'image/tiff',
  'image/avif',
] as const;

const ACCEPTED_EXTENSIONS = [
  '.jpg',
  '.jpeg',
  '.png',
  '.webp',
  '.gif',
  '.tif',
  '.tiff',
  '.avif',
];

/** PROVISIONAL maximum upload size, enforced in the browser before any request. */
export const MAX_IMAGE_BYTES = 10 * 1024 * 1024;

/** Any failure in validating or submitting an analysis. */
/**
 * Statuses where repeating the *same* idempotent GET can legitimately
 * succeed: a request/proxy timeout (408/425), a rate limiter (429) or a
 * transient upstream failure (502/503/504). 501 is excluded — "not
 * implemented" is permanent.
 */
const TRANSIENT_STATUS_CODES = new Set([408, 425, 429, 502, 503, 504]);

export class ApiError extends Error {
  readonly status?: number;
  /**
   * Whether repeating the identical request could plausibly succeed.
   *
   * A transport failure carries no status and is transient by nature (the
   * server may simply have been restarting); the statuses above are
   * transient by definition; every other 4xx means the request itself was
   * wrong and would fail identically forever. A **timeout** is explicitly
   * not retryable by default — the caller already waited the full timeout,
   * and doubling it helps nobody.
   */
  readonly retryable: boolean;

  constructor(message: string, status?: number, retryable?: boolean) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.retryable =
      retryable ?? (status === undefined || TRANSIENT_STATUS_CODES.has(status));
  }
}

/**
 * Browser-side guard: rejects obviously invalid files before any network
 * call. Returns an error message, or null when the file looks acceptable.
 */
export function validateImageFile(file: File | null | undefined): string | null {
  if (!file) return 'No file selected.';

  if (file.size === 0) return 'The selected file is empty.';

  const dotIndex = file.name.lastIndexOf('.');
  const extension = dotIndex > 0 ? file.name.slice(dotIndex).toLowerCase() : '';
  const hasAcceptedExtension = ACCEPTED_EXTENSIONS.includes(extension);

  let acceptable: boolean;
  if (file.type) {
    const knownType = (ACCEPTED_IMAGE_TYPES as readonly string[]).includes(file.type);
    // Some environments report application/octet-stream for images; only
    // trust that when the extension agrees.
    const genericType = file.type === 'application/octet-stream' && hasAcceptedExtension;
    acceptable = knownType || genericType;
  } else {
    // No MIME type reported: fall back to the file extension.
    acceptable = hasAcceptedExtension;
  }

  if (!acceptable) {
    return `"${file.name}" is not a supported image. Use JPEG, PNG, WebP, GIF, TIFF or AVIF.`;
  }

  if (file.size > MAX_IMAGE_BYTES) {
    return `"${file.name}" is ${formatBytes(file.size)}, over the ${formatBytes(MAX_IMAGE_BYTES)} limit.`;
  }

  return null;
}

/** Best-effort extraction of a human-readable error detail from an error response. */
export async function readErrorDetail(response: Response): Promise<string | null> {
  try {
    const text = await response.text();
    if (!text) return null;
    try {
      const parsed = JSON.parse(text) as Record<string, unknown>;
      // Canonical backend envelope (agreed contract):
      //   { "error": { "code": ..., "message": ..., "request_id": ... } }
      // Legacy/fallback shapes kept working: { error: string } and
      // { message: string }. Never render the raw JSON at the user.
      const candidates: unknown[] = [parsed.error, parsed.message];
      for (const candidate of candidates) {
        if (typeof candidate === 'string' && candidate.trim()) {
          return candidate.trim().slice(0, 300);
        }
        if (candidate !== null && typeof candidate === 'object' && 'message' in candidate) {
          const nested = (candidate as { message: unknown }).message;
          if (typeof nested === 'string' && nested.trim()) {
            return nested.trim().slice(0, 300);
          }
        }
      }
    } catch {
      // Not JSON — fall through to the raw text.
    }
    const flat = text.replace(/\s+/g, ' ').trim();
    return flat ? flat.slice(0, 300) : null;
  } catch {
    return null;
  }
}

const SUBMIT_TIMEOUT_MS = 30_000;

/**
 * Submits an image to the backend for analysis.
 *
 * Resolves only when the backend accepted the upload (2xx + provisional
 * envelope); rejects with ApiError for validation failures, transport
 * errors, timeouts, non-2xx responses, and 2xx responses that carry an
 * error or an unrecognised body.
 */
export async function submitAnalysis(file: File): Promise<SubmitAnalysisSuccess> {
  const problem = validateImageFile(file);
  if (problem) throw new ApiError(problem);

  const form = new FormData();
  form.append(ANALYSIS_FILE_FIELD, file, file.name);

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), SUBMIT_TIMEOUT_MS);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${ANALYSIS_ENDPOINT}`, {
      method: 'POST',
      body: form,
      // Never set Content-Type manually: the browser must add the boundary.
      headers: { Accept: 'application/json' },
      // The session lives in an HttpOnly cookie: send it when the API is
      // configured on a different origin (same-origin sends it anyway).
      credentials: 'include',
      signal: controller.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new ApiError('The upload timed out. Please try again.');
    }
    throw new ApiError('Could not reach the server. Check your connection and try again.');
  } finally {
    clearTimeout(timeoutId);
  }

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new ApiError(detail ?? `Upload failed (HTTP ${response.status}).`, response.status);
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (payload === null || typeof payload !== 'object') {
    throw new ApiError('The server returned an unexpected response.', response.status);
  }

  const record = payload as Record<string, unknown>;

  // A 2xx body that carries an error is still an error.
  if (typeof record.error === 'string' && record.error.trim()) {
    throw new ApiError(record.error.trim().slice(0, 300), response.status);
  }

  const analysisId = record.analysisId;
  if (typeof analysisId !== 'string' || analysisId.trim() === '') {
    throw new ApiError('The server response did not include an analysis id.', response.status);
  }

  return {
    analysisId,
    status: record.status === 'processing' ? 'processing' : 'queued',
  };
}

/* ------------------------------------------------------------------
 * A3 — analysis results (PROVISIONAL, same contract note as above)
 * ------------------------------------------------------------------ */

// `partial` is a terminal state, not an error: useful results exist but a
// non-critical stage failed. Rejecting it here would replace a real partial
// result with a generic "could not load" error.
const ANALYSIS_STATES: readonly AnalysisState[] = [
  'queued',
  'processing',
  'completed',
  'partial',
  'failed',
];

/**
 * Fetches the result of a previously submitted analysis.
 *
 * Resolves only for a 2xx JSON body matching the provisional envelope;
 * anything else rejects with ApiError so callers never render a
 * half-parsed result as success.
 */
export async function getAnalysis(analysisId: string): Promise<AnalysisResponse> {
  if (!analysisId.trim()) throw new ApiError('No analysis id provided.');

  let response: Response;
  try {
    response = await fetch(
      `${API_BASE_URL}${ANALYSIS_ENDPOINT}/${encodeURIComponent(analysisId)}`,
      {
        headers: { Accept: 'application/json' },
        credentials: 'include',
      },
    );
  } catch {
    throw new ApiError('Could not reach the server. Check your connection and try again.');
  }

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new ApiError(detail ?? `Could not load the analysis (HTTP ${response.status}).`, response.status);
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (payload === null || typeof payload !== 'object') {
    throw new ApiError('The server returned an unexpected response.', response.status);
  }

  const record = payload as Record<string, unknown>;

  if (typeof record.error === 'string' && record.error.trim() && record.status === undefined) {
    throw new ApiError(record.error.trim().slice(0, 300), response.status);
  }

  const status = record.status;
  if (typeof status !== 'string' || !(ANALYSIS_STATES as readonly string[]).includes(status)) {
    throw new ApiError('The server response did not include a recognised analysis status.', response.status);
  }

  const id = typeof record.id === 'string' && record.id.trim() ? record.id : analysisId;

  return {
    id,
    status: status as AnalysisState,
    progress: typeof record.progress === 'number' ? record.progress : null,
    extractedText: typeof record.extractedText === 'string' ? record.extractedText : null,
    interpretation:
      typeof record.interpretation === 'string' ? record.interpretation : null,
    entities: Array.isArray(record.entities) ? (record.entities as AnalysisEntity[]) : [],
    matches: Array.isArray(record.matches) ? (record.matches as HistoricalMatch[]) : [],
    briefing: normaliseBriefing(record.briefing),
    warnings: Array.isArray(record.warnings) ? (record.warnings as string[]) : [],
    error: typeof record.error === 'string' ? record.error : null,
  };
}

/* ------------------------------------------------------------------
 * R5 — grounded historical briefing
 * ------------------------------------------------------------------ */

const QUERY_KINDS = ['year', 'named', 'general'] as const;

/** Shared normaliser (exported for the R7 account/history service). */
export function asString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null;
}

export function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' ? (value as Record<string, unknown>) : null;
}

/**
 * Normalises a subject image. Returns null when the payload has no usable
 * URL: the UI then shows a placeholder, so an incomplete image can never
 * render as a broken image or as somebody else's photograph.
 */
function normaliseSubjectImage(value: unknown): SubjectImage | null {
  const record = asRecord(value);
  if (!record) return null;
  const url = asString(record.url) ?? asString(record.thumbnailUrl) ?? asString(record.originalUrl);
  if (!url) return null;
  return {
    url,
    proxyUrl: mediaProxyUrl(url),
    thumbnailUrl: asString(record.thumbnailUrl),
    originalUrl: asString(record.originalUrl),
    altText: asString(record.altText),
    caption: asString(record.caption),
    credit: asString(record.credit),
    license: asString(record.license),
    licenseUrl: asString(record.licenseUrl),
    sourcePageUrl: asString(record.sourcePageUrl),
    filePageUrl: asString(record.filePageUrl),
  };
}

/** Canonical entity types the backend may report for a subject/topic. */
const SUBJECT_TYPES = ['person', 'place', 'event', 'date', 'other'] as const;

function normaliseSubjectProfile(value: unknown): SubjectProfile | null {
  const record = asRecord(value);
  const name = record ? asString(record.name) : null;
  if (!record || !name) return null;
  const type = SUBJECT_TYPES.find((candidate) => candidate === record.type) ?? null;
  return {
    name,
    type,
    description: asString(record.description),
    sourceUrl: asString(record.sourceUrl),
    image: normaliseSubjectImage(record.image),
  };
}

/**
 * Normalises related topics. A topic without a stable identity is dropped
 * rather than rendered as a dead label — the whole point of the field is
 * that the card can be opened.
 */
function normaliseRelatedTopics(value: unknown): RelatedTopic[] {
  const topics: RelatedTopic[] = [];
  for (const raw of asArray(value)) {
    const record = asRecord(raw);
    if (!record) continue;
    const id = asString(record.id);
    const name = asString(record.name);
    if (!id || !name) continue;
    topics.push({
      id,
      name,
      type: SUBJECT_TYPES.find((candidate) => candidate === record.type) ?? null,
      description: asString(record.description),
      query: asString(record.query) ?? name,
      sourceUrl: asString(record.sourceUrl),
      relevance: typeof record.relevance === 'number' ? record.relevance : null,
      image: normaliseSubjectImage(record.image),
    });
  }
  return topics;
}

function normaliseSynthesis(value: unknown): BriefingSynthesis | null {
  const record = asRecord(value);
  if (!record) return null;
  const text = asString(record.text);
  const provider = asString(record.provider);
  const method = asString(record.method);
  if (!text || !provider || !method) return null;
  const indexes = asArray(record.sourceIndexes).filter(
    (item): item is number => typeof item === 'number',
  );
  return {
    provider,
    model: asString(record.model) ?? 'unknown',
    method,
    text,
    sourceIndexes: indexes,
  };
}

/**
 * Normalises the briefing payload, dropping anything that does not match the
 * contract.
 *
 * A briefing is quoted source material with citation indexes. Rejecting
 * malformed entries (rather than coercing them) means the UI can never show
 * a sentence that claims a citation the backend did not provide, and a
 * response without a usable briefing simply renders no briefing section.
 */
export function normaliseBriefing(value: unknown): HistoricalBriefing | null {
  const record = asRecord(value);
  if (!record) return null;

  const query = asString(record.query);
  const method = asString(record.method);
  const queryKind = QUERY_KINDS.find((kind) => kind === record.queryKind);
  if (!query || !method || !queryKind) return null;

  const sources: BriefingSource[] = [];
  for (const raw of asArray(record.sources)) {
    const source = asRecord(raw);
    if (!source) continue;
    const index = source.index;
    const title = asString(source.title);
    if (typeof index !== 'number' || !title) continue;
    sources.push({
      index,
      id: asString(source.id) ?? `source-${index}`,
      title,
      url: asString(source.url),
      publisher: asString(source.publisher),
      excerpt: asString(source.excerpt),
      relevance: typeof source.relevance === 'number' ? source.relevance : null,
    });
  }
  const knownIndexes = new Set(sources.map((source) => source.index));

  const keyFacts: KeyFact[] = [];
  for (const raw of asArray(record.keyFacts)) {
    const fact = asRecord(raw);
    const text = fact ? asString(fact.text) : null;
    const sourceIndex = fact?.sourceIndex;
    if (!text || typeof sourceIndex !== 'number' || !knownIndexes.has(sourceIndex)) {
      continue;
    }
    keyFacts.push({ text, sourceIndex });
  }

  const timeline: TimelineEntry[] = [];
  for (const raw of asArray(record.timeline)) {
    const entry = asRecord(raw);
    const date = entry ? asString(entry.date) : null;
    const description = entry ? asString(entry.description) : null;
    const sourceIndex = entry?.sourceIndex;
    if (
      !date ||
      !description ||
      typeof sourceIndex !== 'number' ||
      !knownIndexes.has(sourceIndex)
    ) {
      continue;
    }
    timeline.push({ date, description, sourceIndex });
  }

  const strings = (input: unknown): string[] =>
    asArray(input)
      .map((item) => asString(item))
      .filter((item): item is string => item !== null);

  return {
    query,
    queryKind,
    method,
    summary: asString(record.summary),
    explanation: asString(record.explanation),
    keyFacts,
    timeline,
    people: strings(record.people),
    related: strings(record.related),
    relatedTopics: normaliseRelatedTopics(record.relatedTopics),
    subject: normaliseSubjectProfile(record.subject),
    synthesis: normaliseSynthesis(record.synthesis),
    sources,
    uncertainty: strings(record.uncertainty),
  };
}

/* ------------------------------------------------------------------
 * A4 — graph & evidence (PROVISIONAL, same contract note as above)
 * ------------------------------------------------------------------ */

/** Options for a JSON GET; `timeoutMs` aborts a request that never answers. */
export interface JsonRequestOptions {
  timeoutMs?: number;
}

/** Where the API actually lives, for a message the user can act on. */
function apiOriginLabel(): string {
  if (API_BASE_URL) return API_BASE_URL;
  return typeof window !== 'undefined' ? window.location.origin : 'this origin';
}

export async function getJson<T>(
  url: string,
  description: string,
  normalise: (record: Record<string, unknown>) => T,
  options: JsonRequestOptions = {},
): Promise<T> {
  const controller = options.timeoutMs ? new AbortController() : null;
  const timeoutId = controller
    ? setTimeout(() => controller.abort(), options.timeoutMs)
    : null;

  let response: Response;
  try {
    response = await fetch(url, {
      headers: { Accept: 'application/json' },
      credentials: 'include',
      ...(controller ? { signal: controller.signal } : {}),
    });
  } catch (error) {
    // An aborted request is a timeout; every other rejection is a transport
    // failure (server down, DNS, TLS, CORS, mixed content) — different causes
    // that deserve different words, instead of one blanket message.
    if (controller?.signal.aborted) {
      const seconds = Math.round((options.timeoutMs ?? 0) / 1000);
      // Not retryable: the caller already waited the whole timeout, so a
      // silent second attempt would only double the wait before the same
      // message. (The backend retries the provider well inside this.)
      throw new ApiError(
        `The server did not answer within ${seconds} seconds. It may be busy — try again.`,
        undefined,
        false,
      );
    }
    if (import.meta.env.DEV) {
      console.warn(
        `[heritagegraph] ${description}: the request never reached the API`,
        { url, error, api: apiOriginLabel() },
      );
    }
    throw new ApiError(
      `Could not reach the server (${apiOriginLabel()}). ` +
        'Check that the backend is running, then try again.',
    );
  } finally {
    if (timeoutId) clearTimeout(timeoutId);
  }

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new ApiError(detail ?? `${description} (HTTP ${response.status}).`, response.status);
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (payload === null || typeof payload !== 'object') {
    throw new ApiError('The server returned an unexpected response.', response.status);
  }

  return normalise(payload as Record<string, unknown>);
}

/** Retry backoff: exponential from this base, capped, plus jitter. */
const RETRY_BASE_DELAY_MS = 400;
const RETRY_MAX_DELAY_MS = 4_000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Whether a failed request is worth repeating (see `ApiError.retryable`).
 *
 * Anything that is not an `ApiError` — a normalisation failure, a coding
 * error — is rethrown untouched rather than retried.
 */
export function isTransientApiFailure(error: unknown): boolean {
  return error instanceof ApiError && error.retryable;
}

/** Options for a GET that recovers from a transient failure by itself. */
export interface ResilientJsonRequestOptions extends JsonRequestOptions {
  /** Extra attempts after a transient failure (clamped to 0..2). */
  transientRetries?: number;
}

/**
 * `getJson`, plus transparent recovery from transient failures.
 *
 * The live historical source is a public API: a 429 or a momentary 5xx on
 * the first attempt used to surface straight to the user, who then had to
 * press "Search" again — the exact "first attempt fails, repeating works"
 * symptom. A short, bounded, jittered retry inside the transport means the
 * first request normally just succeeds.
 *
 * Permanent failures (4xx), validation errors and normalisation errors are
 * rethrown immediately: they are not transient, and retrying them would
 * only delay the user's answer.
 *
 * Provider-side `Retry-After` is honoured by the backend, which owns the
 * provider connection; this layer deliberately uses backoff plus jitter so
 * it never waits on a header it cannot read from a thrown error.
 */
export async function getJsonWithRetry<T>(
  url: string,
  description: string,
  normalise: (record: Record<string, unknown>) => T,
  options: ResilientJsonRequestOptions = {},
): Promise<T> {
  const extraAttempts = Math.max(
    0,
    Math.min(2, Math.floor(options.transientRetries ?? 0)),
  );

  for (let attempt = 0; ; attempt += 1) {
    try {
      return await getJson(url, description, normalise, options);
    } catch (error) {
      if (attempt >= extraAttempts || !isTransientApiFailure(error)) throw error;
      const base = Math.min(RETRY_BASE_DELAY_MS * 2 ** attempt, RETRY_MAX_DELAY_MS);
      const delay = Math.round(base + base * 0.25 * Math.random());
      if (import.meta.env.DEV) {
        console.warn(
          `[heritagegraph] ${description}: transient failure, retrying in ${delay}ms`,
          error,
        );
      }
      await sleep(delay);
    }
  }
}

/** PROVISIONAL: fetches the knowledge graph of an analysis. */
export async function getAnalysisGraph(analysisId: string): Promise<GraphResponse> {
  if (!analysisId.trim()) throw new ApiError('No analysis id provided.');

  return getJson(
    `${API_BASE_URL}${ANALYSIS_ENDPOINT}/${encodeURIComponent(analysisId)}/graph`,
    'Could not load the graph',
    (record) => {
      if (typeof record.error === 'string' && record.error.trim()) {
        throw new ApiError(record.error.trim().slice(0, 300));
      }
      return {
        analysisId: typeof record.analysisId === 'string' ? record.analysisId : analysisId,
        nodes: Array.isArray(record.nodes) ? (record.nodes as GraphNode[]) : [],
        edges: Array.isArray(record.edges) ? (record.edges as GraphEdge[]) : [],
      };
    },
  );
}

/** PROVISIONAL: fetches the evidence behind one relationship. */
export async function getRelationshipEvidence(
  analysisId: string,
  relationshipId: string,
): Promise<EvidenceResponse> {
  if (!analysisId.trim() || !relationshipId.trim()) {
    throw new ApiError('An analysis id and a relationship id are required.');
  }

  return getJson(
    `${API_BASE_URL}${ANALYSIS_ENDPOINT}/${encodeURIComponent(analysisId)}/relationships/${encodeURIComponent(relationshipId)}/evidence`,
    'Could not load the evidence',
    (record) => {
      if (typeof record.error === 'string' && record.error.trim()) {
        throw new ApiError(record.error.trim().slice(0, 300));
      }
      return {
        relationshipId:
          typeof record.relationshipId === 'string' && record.relationshipId
            ? record.relationshipId
            : relationshipId,
        verificationStatus:
          typeof record.verificationStatus === 'string' ? record.verificationStatus : 'unverified',
        summary: typeof record.summary === 'string' ? record.summary : null,
        evidence: Array.isArray(record.evidence) ? (record.evidence as EvidenceItem[]) : [],
        notes: Array.isArray(record.notes) ? (record.notes as string[]) : [],
      };
    },
  );
}

/* ------------------------------------------------------------------
 * A5 — text search (PROVISIONAL, same contract note as above)
 * ------------------------------------------------------------------ */

/** PROVISIONAL: text-search endpoint over historical records. */
export const SEARCH_ENDPOINT = '/api/search';

/** Minimum query length accepted by the browser-side guard. */
export const MIN_SEARCH_QUERY_LENGTH = 2;

/**
 * A live search waits on the historical source, so it is allowed to take a
 * while — but not forever: an unanswered request must end in a message, not
 * an interface stuck on "Searching…".
 */
export const SEARCH_TIMEOUT_MS = 30_000;

/**
 * Runs a text search over historical records.
 *
 * Rejects with ApiError for invalid input (before any request), transport
 * failures, and non-2xx or error-carrying responses — so callers can keep
 * "the search failed" strictly distinct from "nothing matched" (an empty
 * `results` array).
 */
export async function searchHistoricalRecords(query: string): Promise<SearchResponse> {
  const trimmed = query.trim();
  if (trimmed.length < MIN_SEARCH_QUERY_LENGTH) {
    throw new ApiError(
      `Enter at least ${MIN_SEARCH_QUERY_LENGTH} characters to search.`,
    );
  }

  return getJsonWithRetry(
    `${API_BASE_URL}${SEARCH_ENDPOINT}?query=${encodeURIComponent(trimmed)}`,
    'Search failed',
    (record) => {
      if (typeof record.error === 'string' && record.error.trim()) {
        throw new ApiError(record.error.trim().slice(0, 300));
      }
      // PROVISIONAL envelope: { query?, results: HistoricalMatch[] }.
      // `records` is accepted as a fallback key.
      const raw = Array.isArray(record.results)
        ? record.results
        : Array.isArray(record.records)
          ? record.records
          : [];
      return {
        query: typeof record.query === 'string' && record.query ? record.query : trimmed,
        results: raw as HistoricalMatch[],
        briefing: normaliseBriefing(record.briefing),
        notice: asString(record.notice),
      };
    },
    { timeoutMs: SEARCH_TIMEOUT_MS, transientRetries: 1 },
  );
}
