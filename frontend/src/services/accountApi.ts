/**
 * R7 — account, history, export and text-search-graph API.
 *
 * Split from `api.ts` (the analysis/search transport) so the authenticated
 * surface has one home. Every request sends the HttpOnly session cookie
 * (`credentials: 'include'`); no token is ever read or stored by the client.
 *
 * Response parsing follows the same rule as `api.ts`: a value is returned
 * only for a response whose shape matches the contract, and everything else
 * rejects with `ApiError` — so the UI can never render a half-parsed account,
 * history row or export as a success.
 */

import {
  API_BASE_URL,
  ApiError,
  getJsonWithRetry,
  normaliseBriefing,
  readErrorDetail,
  MIN_SEARCH_QUERY_LENGTH,
  SEARCH_ENDPOINT,
  asArray,
  asRecord,
  asString,
  type AnalysisEntity,
  type AnalysisResponse,
  type AnalysisState,
  type GraphEdge,
  type GraphEvidence,
  type GraphNode,
  type GraphResponse,
  type HistoricalBriefing,
  type HistoricalMatch,
  type SearchGraphEdge,
  type SearchGraphResponse,
} from './api';

// Re-exported so callers of this module get one import for the transport
// error type (it is defined once, in api.ts).
export { ApiError } from './api';

/** Backend endpoint group for accounts and sessions. */
export const AUTH_ENDPOINT = '/api/auth';

/** Backend endpoint group for history and exports. */
export const HISTORY_ENDPOINT = '/api/history';

/** Default page size when listing history. */
export const HISTORY_PAGE_SIZE = 20;

/* ------------------------------------------------------------------
 * Accounts and sessions
 * ------------------------------------------------------------------ */

/** The signed-in user, as the backend reports it (never a password hash). */
export interface AuthUser {
  id: string;
  email: string;
  displayName?: string | null;
}

export interface SignupInput {
  email: string;
  password: string;
  displayName?: string;
}

function normaliseAuthUser(value: unknown): AuthUser | null {
  const record = asRecord(value);
  const user = record ? asRecord(record.user) : null;
  const id = user ? asString(user.id) : null;
  const email = user ? asString(user.email) : null;
  if (!id || !email) return null;
  return { id, email, displayName: asString(user?.displayName) };
}

/** JSON request helper: cookie credentials + the canonical error envelope. */
async function sendJson(
  url: string,
  init: RequestInit,
  description: string,
): Promise<Record<string, unknown>> {
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      credentials: 'include',
      headers: {
        Accept: 'application/json',
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...(init.headers ?? {}),
      },
    });
  } catch {
    throw new ApiError('Could not reach the server. Check your connection and try again.');
  }

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new ApiError(detail ?? `${description} (HTTP ${response.status}).`, response.status);
  }

  if (response.status === 204) return {};

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError('The server returned an unexpected response.', response.status);
  }
  return asRecord(payload) ?? {};
}

/** Registers an account; on success the session cookie is already set. */
export async function signup(input: SignupInput): Promise<AuthUser> {
  const payload = await sendJson(
    `${API_BASE_URL}${AUTH_ENDPOINT}/signup`,
    { method: 'POST', body: JSON.stringify(input) },
    'Could not create your account',
  );
  const user = normaliseAuthUser(payload);
  if (!user) throw new ApiError('The server did not return the new account.');
  return user;
}

/** Signs in. Failures are generic; the backend never says which field failed. */
export async function login(input: { email: string; password: string }): Promise<AuthUser> {
  const payload = await sendJson(
    `${API_BASE_URL}${AUTH_ENDPOINT}/login`,
    { method: 'POST', body: JSON.stringify(input) },
    'Could not sign in',
  );
  const user = normaliseAuthUser(payload);
  if (!user) throw new ApiError('The server did not return the signed-in account.');
  return user;
}

/** Destroys the server-side session and clears the cookie. */
export async function logout(): Promise<void> {
  await sendJson(
    `${API_BASE_URL}${AUTH_ENDPOINT}/logout`,
    { method: 'POST' },
    'Could not sign out',
  );
}

/**
 * The current session's user, or null when signed out.
 *
 * A 401 is the expected "signed out" answer, not an error; anything else
 * (network failure, 503) rejects, so the UI can tell "not signed in" apart
 * from "sign-in state could not be checked".
 */
export async function getCurrentUser(): Promise<AuthUser | null> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${AUTH_ENDPOINT}/me`, {
      headers: { Accept: 'application/json' },
      credentials: 'include',
    });
  } catch {
    throw new ApiError('Could not reach the server. Check your connection and try again.');
  }

  if (response.status === 401) return null;

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new ApiError(
      detail ?? `Could not check your session (HTTP ${response.status}).`,
      response.status,
    );
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError('The server returned an unexpected response.', response.status);
  }
  return normaliseAuthUser(payload);
}

/* ------------------------------------------------------------------
 * Personal analysis history
 * ------------------------------------------------------------------ */

export interface HistoryItem {
  id: string;
  kind: 'text_search' | 'image_analysis';
  query?: string | null;
  analysisId?: string | null;
  status: string;
  summary?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface HistoryListResponse {
  items: HistoryItem[];
  total: number;
  limit: number;
  offset: number;
}

/** Stored result of a text search, replayed without re-running it. */
export interface SearchSnapshot {
  query: string;
  results: HistoricalMatch[];
  briefing?: HistoricalBriefing | null;
  notice?: string | null;
}

export interface HistoryDetail {
  entry: HistoryItem;
  analysis: AnalysisResponse | null;
  graph: GraphResponse | null;
  search: SearchSnapshot | null;
  /** Honest notes about what this stored record cannot show. */
  limitations: string[];
}

function normaliseHistoryItem(value: unknown): HistoryItem | null {
  const record = asRecord(value);
  if (!record) return null;
  const id = asString(record.id);
  const kind =
    record.kind === 'image_analysis'
      ? 'image_analysis'
      : record.kind === 'text_search'
        ? 'text_search'
        : null;
  if (!id || !kind) return null;
  return {
    id,
    kind,
    query: asString(record.query),
    analysisId: asString(record.analysisId),
    status: asString(record.status) ?? 'queued',
    summary: asString(record.summary),
    createdAt: asString(record.createdAt) ?? '',
    updatedAt: asString(record.updatedAt) ?? '',
  };
}

/** Lists the signed-in user's history (newest first). */
export async function listHistory(
  params: { limit?: number; offset?: number; kind?: 'text_search' | 'image_analysis' } = {},
): Promise<HistoryListResponse> {
  const query = new URLSearchParams();
  query.set('limit', String(params.limit ?? HISTORY_PAGE_SIZE));
  query.set('offset', String(params.offset ?? 0));
  if (params.kind) query.set('kind', params.kind);

  const payload = await sendJson(
    `${API_BASE_URL}${HISTORY_ENDPOINT}?${query.toString()}`,
    { method: 'GET' },
    'Could not load your history',
  );

  const items = asArray(payload.items)
    .map(normaliseHistoryItem)
    .filter((item): item is HistoryItem => item !== null);

  return {
    items,
    total: typeof payload.total === 'number' ? payload.total : items.length,
    limit: typeof payload.limit === 'number' ? payload.limit : items.length,
    offset: typeof payload.offset === 'number' ? payload.offset : 0,
  };
}

/** Loads one history record with everything needed to reopen it. */
export async function getHistoryDetail(entryId: string): Promise<HistoryDetail> {
  if (!entryId.trim()) throw new ApiError('No history id provided.');

  const payload = await sendJson(
    `${API_BASE_URL}${HISTORY_ENDPOINT}/${encodeURIComponent(entryId)}`,
    { method: 'GET' },
    'Could not load that record',
  );

  const entry = normaliseHistoryItem(payload.entry);
  if (!entry) throw new ApiError('The server did not return a usable history record.');

  const analysisRecord = asRecord(payload.analysis);
  const graphRecord = asRecord(payload.graph);
  const searchRecord = asRecord(payload.search);

  return {
    entry,
    analysis: analysisRecord
      ? {
          id: asString(analysisRecord.id) ?? entry.analysisId ?? '',
          status: (asString(analysisRecord.status) ?? 'completed') as AnalysisState,
          progress: typeof analysisRecord.progress === 'number' ? analysisRecord.progress : null,
          extractedText: asString(analysisRecord.extractedText),
          interpretation: asString(analysisRecord.interpretation),
          entities: Array.isArray(analysisRecord.entities)
            ? (analysisRecord.entities as AnalysisEntity[])
            : [],
          matches: Array.isArray(analysisRecord.matches)
            ? (analysisRecord.matches as HistoricalMatch[])
            : [],
          briefing: normaliseBriefing(analysisRecord.briefing),
          warnings: asArray(analysisRecord.warnings).filter(
            (item): item is string => typeof item === 'string',
          ),
          error: asString(analysisRecord.error),
        }
      : null,
    graph: graphRecord
      ? {
          analysisId: asString(graphRecord.analysisId) ?? entry.analysisId ?? '',
          nodes: Array.isArray(graphRecord.nodes) ? (graphRecord.nodes as GraphNode[]) : [],
          edges: Array.isArray(graphRecord.edges) ? (graphRecord.edges as GraphEdge[]) : [],
        }
      : null,
    search: searchRecord
      ? {
          query: asString(searchRecord.query) ?? entry.query ?? '',
          results: Array.isArray(searchRecord.results)
            ? (searchRecord.results as HistoricalMatch[])
            : [],
          briefing: normaliseBriefing(searchRecord.briefing),
          notice: asString(searchRecord.notice),
        }
      : null,
    limitations: asArray(payload.limitations).filter(
      (item): item is string => typeof item === 'string',
    ),
  };
}

/** Same-origin URL of the PDF export for one history record. */
export function historyEntryPdfUrl(entryId: string): string {
  return `${API_BASE_URL}${HISTORY_ENDPOINT}/${encodeURIComponent(entryId)}/pdf`;
}

/** Same-origin URL of the PDF export for the whole history (or a selection). */
export function historyPdfUrl(entryIds?: string[]): string {
  const base = `${API_BASE_URL}${HISTORY_ENDPOINT}/export/pdf`;
  if (!entryIds || entryIds.length === 0) return base;
  return `${base}?ids=${encodeURIComponent(entryIds.join(','))}`;
}

/** A readable download name (the server also sets one; this is the fallback). */
export function pdfFilename(kind: 'analysis' | 'history' | 'entry', date = new Date()): string {
  const stamp = date.toISOString().slice(0, 10);
  const stem = kind === 'history' ? 'heritagegraph-history' : 'heritagegraph-analysis';
  return `${stem}-${stamp}.pdf`;
}

/**
 * Downloads a server-generated PDF.
 *
 * The download is verified *before* it is reported as a success: the response
 * must be a 2xx PDF (checked by its `%PDF-` magic bytes), otherwise an
 * ApiError is thrown and nothing is saved. That is why the export fetches a
 * blob instead of assigning `location.href`.
 */
export async function downloadPdf(url: string, filename: string): Promise<void> {
  let response: Response;
  try {
    response = await fetch(url, {
      headers: { Accept: 'application/pdf' },
      credentials: 'include',
    });
  } catch {
    throw new ApiError('Could not reach the server. Check your connection and try again.');
  }

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new ApiError(detail ?? `The export failed (HTTP ${response.status}).`, response.status);
  }

  const blob = await response.blob();
  if (blob.size < 5) throw new ApiError('The server returned an empty export.');

  const header = new Uint8Array(await blob.slice(0, 5).arrayBuffer());
  const magic = String.fromCharCode(...header);
  if (magic !== '%PDF-') {
    throw new ApiError('The server did not return a PDF document.');
  }

  const objectUrl = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement('a');
    anchor.href = objectUrl;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

/* ------------------------------------------------------------------
 * Knowledge graph for a historical text search
 * ------------------------------------------------------------------ */

function normaliseGraphEvidence(value: unknown): GraphEvidence | null {
  const item = asRecord(value);
  const source = item ? asString(item.source) : null;
  if (!item || !source) return null;
  return { source, excerpt: asString(item.excerpt), url: asString(item.url) };
}

/** Fetches the knowledge graph of a historical text search. */
export async function searchGraph(query: string): Promise<SearchGraphResponse> {
  const trimmed = query.trim();
  if (trimmed.length < MIN_SEARCH_QUERY_LENGTH) {
    throw new ApiError(`Enter at least ${MIN_SEARCH_QUERY_LENGTH} characters to search.`);
  }

  // Same transport rule as the search itself: one transparent retry of a
  // transient source failure instead of making the user switch tabs again.
  return getJsonWithRetry(
    `${API_BASE_URL}${SEARCH_ENDPOINT}/graph?query=${encodeURIComponent(trimmed)}`,
    'Could not build the knowledge graph',
    (record) => {
      const edges: SearchGraphEdge[] = [];
      for (const raw of asArray(record.edges)) {
        const edge = asRecord(raw);
        if (!edge) continue;
        const id = asString(edge.id);
        const source = asString(edge.source);
        const target = asString(edge.target);
        if (!id || !source || !target) continue;
        edges.push({
          id,
          source,
          target,
          label: asString(edge.label),
          verificationStatus: asString(edge.verificationStatus) ?? 'unverified',
          evidence: asArray(edge.evidence)
            .map(normaliseGraphEvidence)
            .filter((item): item is GraphEvidence => item !== null),
        });
      }
      return {
        query: asString(record.query) ?? trimmed,
        nodes: Array.isArray(record.nodes) ? (record.nodes as GraphNode[]) : [],
        edges,
        sources: asArray(record.sources)
          .map(normaliseGraphEvidence)
          .filter((item): item is GraphEvidence => item !== null),
        limitations: asArray(record.limitations).filter(
          (item): item is string => typeof item === 'string',
        ),
        notice: asString(record.notice),
      };
    },
    { transientRetries: 1 },
  );
}
