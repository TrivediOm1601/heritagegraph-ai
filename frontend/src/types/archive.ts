/**
 * Contract types for the Heritage Archive Studio, research workspace and
 * heritage map.
 *
 * Every field mirrors an API response; optional/nullable values are kept as
 * the backend reports them so a missing value is rendered as "unknown" rather
 * than invented.
 */

export type ArchiveFileKind = 'image' | 'pdf' | 'docx' | 'pptx' | 'text' | 'zip';

export type ArchiveFileStatus =
  | 'uploaded'
  | 'queued'
  | 'processing'
  | 'ocr_completed'
  | 'text_extracted'
  | 'analysed'
  | 'completed'
  | 'partial'
  | 'failed';

export type DuplicateKind = 'exact' | 'similar_image' | 'similar_text';

export interface ArchiveStats {
  files: number;
  completed: number;
  partial: number;
  failed: number;
  processing: number;
  pages: number;
  characters: number;
  entities: number;
  timelineEvents: number;
  duplicates: number;
  byStatus: Record<string, number>;
  byKind: Record<string, number>;
  languages: Record<string, number>;
}

export interface ArchiveFileItem {
  id: string;
  archiveId: string;
  parentFileId?: string | null;
  filename: string;
  contentType: string;
  sizeBytes: number;
  kind: ArchiveFileKind;
  status: ArchiveFileStatus;
  statusDetail?: string | null;
  error?: string | null;
  language?: string | null;
  languageScript?: string | null;
  pageCount: number;
  pageLabel: string;
  duplicateOfId?: string | null;
  duplicateKind?: DuplicateKind | null;
  warnings: string[];
  archivePath?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface Archive {
  id: string;
  name: string;
  description?: string | null;
  region?: string | null;
  tags: string[];
  createdAt: string;
  updatedAt: string;
  stats: ArchiveStats;
}

export interface ArchiveDetail extends Archive {
  files: ArchiveFileItem[];
}

export interface RejectedUpload {
  filename: string;
  reason: string;
}

export interface ArchiveUploadResult {
  accepted: ArchiveFileItem[];
  rejected: RejectedUpload[];
  warnings: string[];
  message: string;
}

export interface ExtractedPage {
  pageNumber: number;
  text: string;
  charCount: number;
  textSource: 'text-layer' | 'ocr' | 'extracted' | 'none' | string;
}

export interface FileEntity {
  id: string;
  name: string;
  type: string;
  pageNumber?: number | null;
  confidence?: number | null;
  extractionMethod: string;
  excerpt?: string | null;
}

export interface TimelineEvent {
  id: string;
  title: string;
  description?: string | null;
  dateText: string;
  yearStart?: number | null;
  yearEnd?: number | null;
  era: 'ce' | 'bce' | 'unknown';
  precision: 'exact' | 'approximate' | 'century' | 'range' | 'unknown';
  sourcePage?: number | null;
  evidence?: string | null;
  verificationStatus: string;
  archiveId?: string | null;
  fileId?: string | null;
  sourceFile?: string | null;
}

export interface ArchiveFileDetail {
  file: ArchiveFileItem;
  archiveId: string;
  archiveName: string;
  pages: ExtractedPage[];
  entities: FileEntity[];
  timeline: TimelineEvent[];
  duplicateOf?: ArchiveFileItem | null;
  parentFile?: ArchiveFileItem | null;
  limitations: string[];
}

export interface ArchiveSearchResult {
  fileId: string;
  archiveId: string;
  archiveName: string;
  filename: string;
  kind: string;
  pageNumber: number;
  pageLabel: string;
  excerpt: string;
  score: number;
  language?: string | null;
  textSource: string;
  matchedEntities: Array<{ name: string; type: string; pageNumber?: number | null }>;
}

export interface ArchiveSearchResponse {
  query: string;
  mode: 'keyword';
  results: ArchiveSearchResult[];
  total: number;
  notice?: string | null;
  truncated: boolean;
}

export interface TimelineResponse {
  events: TimelineEvent[];
  total: number;
  limitations: string[];
}

export interface HeritageSourceRef {
  label: string;
  url: string;
}

export interface HeritageSite {
  id: string;
  name: string;
  alternativeNames: string[];
  region: string;
  state: string;
  isUnionTerritory: boolean;
  district?: string | null;
  city?: string | null;
  category: string;
  periodGroup?: string | null;
  period?: string | null;
  architecturalStyle?: string | null;
  unescoStatus: boolean;
  unescoYear?: number | null;
  latitude?: number | null;
  longitude?: number | null;
  coordinateSource?: string | null;
  coordinateAccuracy: 'verified' | 'approximate';
  verificationStatus: string;
  description?: string | null;
  sourceUrl?: string | null;
  sourceRefs: HeritageSourceRef[];
  imageUrl?: string | null;
  imageCredit?: string | null;
  imageLicense?: string | null;
  dataset: string;
  storyCount: number;
  storyCategories: string[];
  featuredStories: HeritageFeaturedStory[];
}

export interface HeritageFeaturedStory {
  id: string;
  title: string;
  category: string;
  categoryLabel: string;
  narrativeType: string;
  narrativeTypeLabel: string;
  isCommunity: boolean;
}

export interface HeritageStateFacet {
  name: string;
  region: string;
  isUnionTerritory: boolean;
  count: number;
}

export interface HeritageCountFacet {
  name: string;
  count: number;
}

export interface HeritageSummary {
  totalSites: number;
  statesRepresented: number;
  unionTerritoriesRepresented: number;
  categories: number;
  verifiedRecords: number;
  approximateCoordinates: number;
  unescoSites: number;
  stories: number;
  sitesWithStories: number;
}

export interface HeritageStoryFacet {
  name: string;
  label: string;
  count: number;
}

export interface HeritageFacets {
  states: HeritageStateFacet[];
  regions: HeritageCountFacet[];
  categories: HeritageCountFacet[];
  periods: HeritageCountFacet[];
  storyCategories: HeritageStoryFacet[];
}

export interface HeritageSitesResponse {
  sites: HeritageSite[];
  total: number;
  limit: number;
  offset: number;
  dataset: string;
  notice: string;
  summary: HeritageSummary;
  facets: HeritageFacets;
}

export interface HeritageRouteLeg {
  from: string;
  to: string;
  distanceKm: number;
}

export interface HeritageRoute {
  stops: HeritageSite[];
  legs: HeritageRouteLeg[];
  totalDistanceKm: number;
  distanceMethod: string;
  ordering: 'selection' | 'distance';
  notice: string;
  excludedSiteIds: string[];
}

/* ------------------------------------------------------------------ */
/* Stories & Legends                                                   */
/* ------------------------------------------------------------------ */

export interface StorySource {
  id: string;
  title: string;
  author?: string | null;
  sourceType: string;
  published?: string | null;
  url?: string | null;
  excerpt?: string | null;
  locator?: string | null;
  licence?: string | null;
}

export interface StoryVersion {
  id: string;
  label: string;
  summary: string;
  narrative?: string | null;
  tradition?: string | null;
  language: string;
  community?: string | null;
  differences?: string | null;
  verificationStatus: string;
  sources: StorySource[];
}

export interface StorySummary {
  id: string;
  siteId: string;
  title: string;
  summary: string;
  category: string;
  categoryLabel: string;
  narrativeType: string;
  narrativeTypeLabel: string;
  tradition?: string | null;
  language: string;
  languageLabel: string;
  community?: string | null;
  period?: string | null;
  verificationStatus: string;
  provenance: string;
  isCommunity: boolean;
  versionCount: number;
  sourceCount: number;
}

export interface StoryDetail extends StorySummary {
  siteName: string;
  siteState?: string | null;
  narrative: string;
  culturalBackground?: string | null;
  relatedEntities: string[];
  relatedSites: string[];
  sources: StorySource[];
  versions: StoryVersion[];
  relatedStories: StorySummary[];
}

export interface SiteStoriesResponse {
  siteId: string;
  siteName: string;
  total: number;
  stories: StorySummary[];
  categories: HeritageStoryFacet[];
  notice: string;
}

export interface StoryAskResponse {
  answer: string;
  matchedStories: StorySummary[];
  citations: string[];
  limitation?: string | null;
}

export interface StorySubmission {
  story: StorySummary;
  status: string;
  message: string;
}

export interface ResearchCollection {
  id: string;
  name: string;
  description?: string | null;
  itemCount: number;
  noteCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface CollectionItemRef {
  type: 'archive' | 'file';
  id: string;
  name: string;
  addedAt: string;
}

export interface ResearchNote {
  id: string;
  body: string;
  subjectQuery?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ResearchCollectionDetail extends ResearchCollection {
  items: CollectionItemRef[];
  notes: ResearchNote[];
}
