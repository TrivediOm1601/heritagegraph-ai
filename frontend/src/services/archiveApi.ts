/**
 * Heritage Archive Studio, research workspace and heritage-map API client.
 *
 * Same transport rules as the rest of the app: relative `/api` URLs on the
 * page's own origin, the HttpOnly session cookie is sent with every request,
 * and a response is only treated as success when it is a 2xx with the shape
 * this module understands. Errors surface the backend's safe `error.message`.
 */

import { API_BASE_URL, ApiError, readErrorDetail } from './api';
import type {
  Archive,
  ArchiveDetail,
  ArchiveFileDetail,
  ArchiveFileItem,
  ArchiveSearchResponse,
  ArchiveStats,
  ArchiveUploadResult,
  HeritageRoute,
  HeritageSite,
  HeritageSitesResponse,
  SiteStoriesResponse,
  StoryDetail,
  StoryAskResponse,
  StorySubmission,
  ResearchCollectionDetail,
  ResearchCollection,
  ResearchNote,
  TimelineResponse,
} from '../types/archive';

export const ARCHIVE_ENDPOINT = '/api/archives';
export const ARCHIVE_SEARCH_ENDPOINT = '/api/archive-search';
export const HERITAGE_ENDPOINT = '/api/heritage';
export const COLLECTION_ENDPOINT = '/api/collections';

/** Accept attribute for the archive file input (mirrors the backend list). */
export const ARCHIVE_ACCEPT =
  '.jpg,.jpeg,.png,.webp,.gif,.tif,.tiff,.avif,.pdf,.docx,.pptx,.txt,.md,.markdown,.zip,image/*,application/pdf,text/plain';

/** Maximum number of files the upload endpoint accepts per request. */
export const MAX_FILES_PER_UPLOAD = 20;

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object'
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null;
}

async function sendJson(
  url: string,
  init: RequestInit = {},
  description = 'The request failed',
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
    throw new ApiError(
      detail ?? `${description} (HTTP ${response.status}).`,
      response.status,
    );
  }
  if (response.status === 204) return {};

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError('The server returned an unexpected response.', response.status);
  }
  const record = asRecord(payload);
  if (!record) {
    throw new ApiError('The server returned an unexpected response.', response.status);
  }
  return record;
}

/* ------------------------------------------------------------------ */
/* Archives                                                            */
/* ------------------------------------------------------------------ */

export interface CreateArchiveInput {
  name: string;
  description?: string;
  region?: string;
  tags?: string[];
}

export async function listArchives(): Promise<Archive[]> {
  const payload = await sendJson(
    `${API_BASE_URL}${ARCHIVE_ENDPOINT}`,
    {},
    'Could not load your archives',
  );
  return Array.isArray(payload.items) ? (payload.items as Archive[]) : [];
}

export async function createArchive(input: CreateArchiveInput): Promise<ArchiveDetail> {
  const payload = await sendJson(
    `${API_BASE_URL}${ARCHIVE_ENDPOINT}`,
    { method: 'POST', body: JSON.stringify(input) },
    'Could not create the archive',
  );
  return payload as unknown as ArchiveDetail;
}

export async function getArchive(archiveId: string): Promise<ArchiveDetail> {
  const payload = await sendJson(
    `${API_BASE_URL}${ARCHIVE_ENDPOINT}/${encodeURIComponent(archiveId)}`,
    {},
    'Could not load the archive',
  );
  return payload as unknown as ArchiveDetail;
}

export async function renameArchive(
  archiveId: string,
  input: { name?: string; description?: string; region?: string; tags?: string[] },
): Promise<ArchiveDetail> {
  const payload = await sendJson(
    `${API_BASE_URL}${ARCHIVE_ENDPOINT}/${encodeURIComponent(archiveId)}`,
    { method: 'PATCH', body: JSON.stringify(input) },
    'Could not update the archive',
  );
  return payload as unknown as ArchiveDetail;
}

export async function deleteArchive(archiveId: string): Promise<void> {
  await sendJson(
    `${API_BASE_URL}${ARCHIVE_ENDPOINT}/${encodeURIComponent(archiveId)}`,
    { method: 'DELETE' },
    'Could not remove the archive',
  );
}

/** Uploads files (optionally including a ZIP) to one archive. */
export async function uploadArchiveFiles(
  archiveId: string,
  files: File[],
): Promise<ArchiveUploadResult> {
  if (files.length === 0) throw new ApiError('Choose at least one file to upload.');
  if (files.length > MAX_FILES_PER_UPLOAD) {
    throw new ApiError(`Upload at most ${MAX_FILES_PER_UPLOAD} files at a time.`);
  }

  const form = new FormData();
  for (const file of files) form.append('files', file, file.name);

  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 180_000);
  let response: Response;
  try {
    response = await fetch(
      `${API_BASE_URL}${ARCHIVE_ENDPOINT}/${encodeURIComponent(archiveId)}/files`,
      {
        method: 'POST',
        body: form,
        headers: { Accept: 'application/json' },
        credentials: 'include',
        signal: controller.signal,
      },
    );
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new ApiError('The upload timed out. Please try again.');
    }
    throw new ApiError('Could not reach the server. Check your connection and try again.');
  } finally {
    window.clearTimeout(timeout);
  }

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new ApiError(detail ?? `Upload failed (HTTP ${response.status}).`, response.status);
  }

  const payload = asRecord(await response.json().catch(() => null));
  if (!payload) throw new ApiError('The server returned an unexpected response.');
  return {
    accepted: Array.isArray(payload.accepted) ? (payload.accepted as ArchiveFileItem[]) : [],
    rejected: Array.isArray(payload.rejected)
      ? (payload.rejected as ArchiveUploadResult['rejected'])
      : [],
    warnings: Array.isArray(payload.warnings)
      ? payload.warnings.filter((item): item is string => typeof item === 'string')
      : [],
    message: asString(payload.message) ?? 'Upload accepted.',
  };
}

export async function getArchiveFile(fileId: string): Promise<ArchiveFileDetail> {
  const payload = await sendJson(
    `${API_BASE_URL}/api/archive-files/${encodeURIComponent(fileId)}`,
    {},
    'Could not load the extracted text',
  );
  return payload as unknown as ArchiveFileDetail;
}

export async function reprocessArchiveFile(fileId: string): Promise<void> {
  await sendJson(
    `${API_BASE_URL}/api/archive-files/${encodeURIComponent(fileId)}/reprocess`,
    { method: 'POST' },
    'Could not reprocess the file',
  );
}

export async function deleteArchiveFile(fileId: string): Promise<void> {
  await sendJson(
    `${API_BASE_URL}/api/archive-files/${encodeURIComponent(fileId)}`,
    { method: 'DELETE' },
    'Could not remove the file',
  );
}

export function archiveFileDownloadUrl(fileId: string): string {
  return `${API_BASE_URL}/api/archive-files/${encodeURIComponent(fileId)}/download`;
}

export function archiveReportUrl(archiveId: string): string {
  return `${API_BASE_URL}${ARCHIVE_ENDPOINT}/${encodeURIComponent(archiveId)}/report.pdf`;
}

/** A meaningful, filesystem-safe name for the archive report download. */
export function archiveReportFilename(name: string): string {
  const slug = (name || 'archive')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60) || 'archive';
  const stamp = new Date().toISOString().slice(0, 10);
  return `heritagegraph-${slug}-${stamp}.pdf`;
}

export interface ArchiveSearchParams {
  query: string;
  archiveId?: string;
  language?: string;
  kind?: string;
  entityType?: string;
  limit?: number;
}

export async function searchArchives(
  params: ArchiveSearchParams,
): Promise<ArchiveSearchResponse> {
  const query = new URLSearchParams({ q: params.query.trim() });
  if (params.archiveId) query.set('archiveId', params.archiveId);
  if (params.language) query.set('language', params.language);
  if (params.kind) query.set('kind', params.kind);
  if (params.entityType) query.set('entityType', params.entityType);
  if (params.limit) query.set('limit', String(params.limit));

  const payload = await sendJson(
    `${API_BASE_URL}${ARCHIVE_SEARCH_ENDPOINT}?${query.toString()}`,
    {},
    'Could not search the archive',
  );
  return {
    query: asString(payload.query) ?? params.query,
    mode: 'keyword',
    results: Array.isArray(payload.results)
      ? (payload.results as ArchiveSearchResponse['results'])
      : [],
    total: typeof payload.total === 'number' ? payload.total : 0,
    notice: asString(payload.notice),
    truncated: payload.truncated === true,
  };
}

export async function getArchiveTimeline(archiveId: string): Promise<TimelineResponse> {
  const payload = await sendJson(
    `${API_BASE_URL}${ARCHIVE_ENDPOINT}/${encodeURIComponent(archiveId)}/timeline`,
    {},
    'Could not load the timeline',
  );
  return {
    events: Array.isArray(payload.events)
      ? (payload.events as TimelineResponse['events'])
      : [],
    total: typeof payload.total === 'number' ? payload.total : 0,
    limitations: Array.isArray(payload.limitations)
      ? payload.limitations.filter((item): item is string => typeof item === 'string')
      : [],
  };
}

/* ------------------------------------------------------------------ */
/* Heritage map                                                        */
/* ------------------------------------------------------------------ */

export interface HeritageFilters {
  q?: string;
  state?: string;
  region?: string;
  category?: string;
  periodGroup?: string;
  unesco?: boolean;
  /** Only sites with a published story in this category. */
  story?: string;
  sort?: 'name' | 'state' | 'category' | 'period';
  limit?: number;
  offset?: number;
}

function facetList(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.filter(
        (item): item is Record<string, unknown> =>
          item !== null && typeof item === 'object',
      )
    : [];
}

/**
 * The national heritage dataset (curated demonstration records).
 *
 * Filters and pagination are supported by the backend; the map page loads
 * the bounded curated set once and filters it locally for a responsive map,
 * while the same parameters remain available to other API clients.
 */
export async function listHeritageSites(
  filters: HeritageFilters = {},
): Promise<HeritageSitesResponse> {
  const query = new URLSearchParams();
  if (filters.q) query.set('q', filters.q);
  if (filters.state) query.set('state', filters.state);
  if (filters.region) query.set('region', filters.region);
  if (filters.category) query.set('category', filters.category);
  if (filters.periodGroup) query.set('periodGroup', filters.periodGroup);
  if (filters.unesco !== undefined) query.set('unesco', String(filters.unesco));
  if (filters.story) query.set('story', filters.story);
  if (filters.sort) query.set('sort', filters.sort);
  if (filters.limit) query.set('limit', String(filters.limit));
  if (filters.offset) query.set('offset', String(filters.offset));
  const suffix = query.toString() ? `?${query.toString()}` : '';

  const payload = await sendJson(
    `${API_BASE_URL}${HERITAGE_ENDPOINT}/sites${suffix}`,
    {},
    'Could not load the heritage map',
  );

  const rawSummary =
    payload.summary !== null && typeof payload.summary === 'object'
      ? (payload.summary as Record<string, unknown>)
      : {};
  const count = (value: unknown): number =>
    typeof value === 'number' && Number.isFinite(value) ? value : 0;
  const rawFacets =
    payload.facets !== null && typeof payload.facets === 'object'
      ? (payload.facets as Record<string, unknown>)
      : {};

  return {
    sites: Array.isArray(payload.sites) ? (payload.sites as HeritageSite[]) : [],
    total: count(payload.total),
    limit: count(payload.limit) || 500,
    offset: count(payload.offset),
    dataset: asString(payload.dataset) ?? 'curated-demo-india',
    notice: asString(payload.notice) ?? '',
    summary: {
      totalSites: count(rawSummary.totalSites),
      statesRepresented: count(rawSummary.statesRepresented),
      unionTerritoriesRepresented: count(rawSummary.unionTerritoriesRepresented),
      categories: count(rawSummary.categories),
      verifiedRecords: count(rawSummary.verifiedRecords),
      approximateCoordinates: count(rawSummary.approximateCoordinates),
      unescoSites: count(rawSummary.unescoSites),
      stories: count(rawSummary.stories),
      sitesWithStories: count(rawSummary.sitesWithStories),
    },
    facets: {
      states: facetList(rawFacets.states).map((item) => ({
        name: asString(item.name) ?? '',
        region: asString(item.region) ?? '',
        isUnionTerritory: item.isUnionTerritory === true,
        count: count(item.count),
      })),
      regions: facetList(rawFacets.regions).map((item) => ({
        name: asString(item.name) ?? '',
        count: count(item.count),
      })),
      categories: facetList(rawFacets.categories).map((item) => ({
        name: asString(item.name) ?? '',
        count: count(item.count),
      })),
      periods: facetList(rawFacets.periods).map((item) => ({
        name: asString(item.name) ?? '',
        count: count(item.count),
      })),
      storyCategories: facetList(rawFacets.storyCategories).map((item) => ({
        name: asString(item.name) ?? '',
        label: asString(item.label) ?? asString(item.name) ?? '',
        count: count(item.count),
      })),
    },
  };
}

export async function listSiteStories(
  siteId: string,
  options: { category?: string; language?: string } = {},
): Promise<SiteStoriesResponse> {
  const query = new URLSearchParams();
  if (options.category) query.set('category', options.category);
  if (options.language) query.set('language', options.language);
  const suffix = query.toString() ? `?${query.toString()}` : '';
  const payload = await sendJson(
    `${API_BASE_URL}${HERITAGE_ENDPOINT}/sites/${encodeURIComponent(siteId)}/stories${suffix}`,
    {},
    'Could not load the stories for this site',
  );
  return payload as unknown as SiteStoriesResponse;
}

export async function getStory(storyId: string): Promise<StoryDetail> {
  const payload = await sendJson(
    `${API_BASE_URL}${HERITAGE_ENDPOINT}/stories/${encodeURIComponent(storyId)}`,
    {},
    'Could not load this story',
  );
  return payload as unknown as StoryDetail;
}

export async function askStories(
  siteId: string,
  question: string,
): Promise<StoryAskResponse> {
  return (await sendJson(
    `${API_BASE_URL}${HERITAGE_ENDPOINT}/stories/ask`,
    { method: 'POST', body: JSON.stringify({ siteId, question }) },
    'Could not answer from the story library',
  )) as unknown as StoryAskResponse;
}

export interface StorySubmissionInput {
  title: string;
  narrative: string;
  language: string;
  community?: string;
  tradition?: string;
  sourceNote?: string;
  interviewDate?: string;
  consent: boolean;
}

export async function submitStory(
  siteId: string,
  input: StorySubmissionInput,
): Promise<StorySubmission> {
  return (await sendJson(
    `${API_BASE_URL}${HERITAGE_ENDPOINT}/sites/${encodeURIComponent(siteId)}/stories`,
    { method: 'POST', body: JSON.stringify(input) },
    'Could not submit this story',
  )) as unknown as StorySubmission;
}

export async function buildHeritageRoute(
  siteIds: string[],
  startSiteId?: string,
  order: 'selection' | 'distance' = 'distance',
): Promise<HeritageRoute> {
  const payload = await sendJson(
    `${API_BASE_URL}${HERITAGE_ENDPOINT}/route`,
    {
      method: 'POST',
      body: JSON.stringify({
        siteIds,
        startSiteId: startSiteId ?? siteIds[0],
        order,
      }),
    },
    'Could not build a route',
  );
  return payload as unknown as HeritageRoute;
}

/* ------------------------------------------------------------------ */
/* Research workspace                                                  */
/* ------------------------------------------------------------------ */

export async function listCollections(): Promise<ResearchCollection[]> {
  const payload = await sendJson(
    `${API_BASE_URL}${COLLECTION_ENDPOINT}`,
    {},
    'Could not load your collections',
  );
  return Array.isArray(payload.items) ? (payload.items as ResearchCollection[]) : [];
}

export async function createCollection(input: {
  name: string;
  description?: string;
}): Promise<ResearchCollectionDetail> {
  const payload = await sendJson(
    `${API_BASE_URL}${COLLECTION_ENDPOINT}`,
    { method: 'POST', body: JSON.stringify(input) },
    'Could not create the collection',
  );
  return payload as unknown as ResearchCollectionDetail;
}

export async function getCollection(collectionId: string): Promise<ResearchCollectionDetail> {
  const payload = await sendJson(
    `${API_BASE_URL}${COLLECTION_ENDPOINT}/${encodeURIComponent(collectionId)}`,
    {},
    'Could not load the collection',
  );
  return payload as unknown as ResearchCollectionDetail;
}

export async function addCollectionItem(
  collectionId: string,
  item: { type: 'archive' | 'file'; id: string },
): Promise<ResearchCollectionDetail> {
  const payload = await sendJson(
    `${API_BASE_URL}${COLLECTION_ENDPOINT}/${encodeURIComponent(collectionId)}/items`,
    { method: 'POST', body: JSON.stringify(item) },
    'Could not add that item',
  );
  return payload as unknown as ResearchCollectionDetail;
}

export async function removeCollectionItem(
  collectionId: string,
  item: { type: 'archive' | 'file'; id: string },
): Promise<void> {
  const query = new URLSearchParams({ type: item.type, id: item.id });
  await sendJson(
    `${API_BASE_URL}${COLLECTION_ENDPOINT}/${encodeURIComponent(collectionId)}/items?${query.toString()}`,
    { method: 'DELETE' },
    'Could not remove that item',
  );
}

export async function addCollectionNote(
  collectionId: string,
  input: { body: string; subjectQuery?: string },
): Promise<ResearchNote> {
  const payload = await sendJson(
    `${API_BASE_URL}${COLLECTION_ENDPOINT}/${encodeURIComponent(collectionId)}/notes`,
    { method: 'POST', body: JSON.stringify(input) },
    'Could not save the note',
  );
  return payload as unknown as ResearchNote;
}

export async function deleteCollectionNote(noteId: string): Promise<void> {
  await sendJson(
    `${API_BASE_URL}/api/notes/${encodeURIComponent(noteId)}`,
    { method: 'DELETE' },
    'Could not delete the note',
  );
}

export function collectionReportUrl(collectionId: string): string {
  return `${API_BASE_URL}${COLLECTION_ENDPOINT}/${encodeURIComponent(collectionId)}/report.pdf`;
}

/* ------------------------------------------------------------------ */
/* Small display helpers                                               */
/* ------------------------------------------------------------------ */

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function statsTotal(stats: ArchiveStats | undefined): number {
  return stats ? stats.files : 0;
}
