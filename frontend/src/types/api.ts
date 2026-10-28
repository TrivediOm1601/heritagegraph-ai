/**
 * A5 — the frontend's API contract types.
 *
 * OWNERSHIP NOTE: under the engineering plan this file is the shared API
 * contract maintained by Member 2. Member 1 created it in A5 as a faithful
 * consolidation of the provisional contract that had lived inside
 * services/api.ts (A2–A4), so response shapes have exactly ONE home.
 * Nothing here invents a new shape: every type is moved verbatim, and the
 * search types reuse HistoricalMatch for results. When Member 2's contract
 * lands, this file is the single place to adjust — the service layer
 * adapts, and the display components are already contract-agnostic
 * (primitive props only).
 */

/**
 * Lifecycle states of a submitted analysis.
 *
 * `partial` means useful results exist but a non-critical stage failed; the
 * response's `warnings` say which. `completed` is only reported when every
 * required stage succeeded.
 */
export type AnalysisState =
  | 'queued'
  | 'processing'
  | 'completed'
  | 'partial'
  | 'failed';

/** Success envelope of POST /api/analysis. */
export interface SubmitAnalysisSuccess {
  analysisId: string;
  status: 'queued' | 'processing';
}

/** An entity detected in the image's text. */
export interface AnalysisEntity {
  id: string;
  name: string;
  /** Entity category as reported by the backend (person, place, event, date, ...). */
  type: string;
  /** Optional 0..1 extraction confidence. */
  confidence?: number | null;
}

/**
 * Coarse retrieval-relevance label. Deliberately a band (high/medium/low)
 * rather than a confidence percentage: `score` measures how well the query
 * matches the record's text, nothing more.
 */
export type RelevanceLabel = 'high' | 'medium' | 'low';

/** A historical record matched to the extraction (also used for search results). */
export interface HistoricalMatch {
  id: string;
  title: string;
  /** Free-text year, e.g. "c. 1885". */
  year?: string | null;
  /** Optional 0..1 retrieval-relevance score. */
  score?: number | null;
  /** Where the record comes from (archive, collection, ...). */
  source?: string | null;
  /** Canonical link to the cited source material, when one exists. */
  url?: string | null;
  /** A window of the record's own text around the match (quoted, never generated). */
  excerpt?: string | null;
  /** Band for `score`, so the UI does not show false precision. */
  relevance?: RelevanceLabel | null;
}

/** One source a briefing quotes, cited by index (1..N). */
export interface BriefingSource {
  index: number;
  id: string;
  title: string;
  url?: string | null;
  publisher?: string | null;
  excerpt?: string | null;
  relevance?: number | null;
}

/** One sentence quoted verbatim from the cited source. */
export interface KeyFact {
  text: string;
  sourceIndex: number;
}

/** One dated event exactly as the cited source records it. */
export interface TimelineEntry {
  date: string;
  description: string;
  sourceIndex: number;
}

/**
 * A photograph of the subject, with the provenance that carries it.
 *
 * Every field comes from the source (MediaWiki page-images + Commons file
 * metadata). A field the source did not supply is `null`; the UI then
 * shows a placeholder rather than an unrelated picture or an invented
 * caption/credit/license.
 */
export interface SubjectImage {
  /** Best display URL (a scaled render). */
  url?: string | null;
  /**
   * Same-origin candidate for the same image, derived by the API client
   * (NOT sent by the backend): `/api/media?url=…`. The UI tries it first so
   * image display never depends on the browser being able to reach a
   * third-party image host, and falls back to `url` if it is unavailable.
   */
  proxyUrl?: string | null;
  thumbnailUrl?: string | null;
  originalUrl?: string | null;
  /** Accessible label built from the source's own description. */
  altText?: string | null;
  /** Image description from the file page. */
  caption?: string | null;
  /** Author/attribution exactly as the source records it. */
  credit?: string | null;
  /** License short name, e.g. "CC BY-SA 4.0". */
  license?: string | null;
  licenseUrl?: string | null;
  /** The subject's page (where the image is published). */
  sourcePageUrl?: string | null;
  /** The file's description page — the attribution link. */
  filePageUrl?: string | null;
}

/** Who/what the query is about, as the sources describe it. */
export interface SubjectProfile {
  name: string;
  /**
   * Canonical entity type (person/place/event/date/other) from the source
   * authority — `null` when it is not mapped, never guessed.
   */
  type?: string | null;
  /** The source's own one-line description. */
  description?: string | null;
  sourceUrl?: string | null;
  image?: SubjectImage | null;
}

/**
 * A related subject the user can open — a real identity, not a label.
 * `query` is the exact search that opens it.
 */
export interface RelatedTopic {
  /** Stable identity (source record id, e.g. `wiki:799638`). */
  id: string;
  name: string;
  type?: string | null;
  description?: string | null;
  query: string;
  sourceUrl?: string | null;
  relevance?: number | null;
  image?: SubjectImage | null;
}

/**
 * A generated paragraph over the briefing's quoted statements.
 *
 * Deliberately separate from `summary`/`explanation`: those are always the
 * sources' own wording, while this one was composed by a model — and only
 * ever over statements the page already shows, each sentence citing them.
 */
export interface BriefingSynthesis {
  provider: string;
  model: string;
  method: string;
  text: string;
  /** Citation indexes the summary actually used (into `sources`). */
  sourceIndexes: number[];
}

/**
 * Historical explanation assembled from retrieved sources.
 *
 * `method` states how it was produced. Today it is always
 * `extractive-quotes`: every sentence is selected from a retrieved source
 * and carries that source's citation index, so nothing is generated from a
 * model's recollection.
 */
export interface HistoricalBriefing {
  query: string;
  queryKind: 'year' | 'named' | 'general';
  method: string;
  summary?: string | null;
  explanation?: string | null;
  keyFacts: KeyFact[];
  timeline: TimelineEntry[];
  /** People named in the source's own Births/Deaths sections (year queries). */
  people: string[];
  /** Other retrieved topics related to the query. */
  related: string[];
  /** The same topics with identity, type, description and thumbnail. */
  relatedTopics: RelatedTopic[];
  /** What the query is about, with its photograph when the source has one. */
  subject?: SubjectProfile | null;
  /** Generated summary; absent unless a provider was configured and its
   *  answer passed the grounding checks. */
  synthesis?: BriefingSynthesis | null;
  sources: BriefingSource[];
  uncertainty: string[];
}

/** Full result payload of GET /api/analysis/{id}. */
export interface AnalysisResponse {
  id: string;
  status: AnalysisState;
  /** Progress through processing, 0..1 when the backend reports it. */
  progress?: number | null;
  /** Text extracted from the image (OCR). */
  extractedText?: string | null;
  /**
   * AI-generated reading of the image: candidate identification(s),
   * visual clues, alternatives and uncertainty. A candidate, never a
   * verified historical claim.
   */
  interpretation?: string | null;
  entities?: AnalysisEntity[] | null;
  matches?: HistoricalMatch[] | null;
  /** Grounded historical explanation for the identified entity. */
  briefing?: HistoricalBriefing | null;
  /** Non-fatal warnings / uncertainty notes from the backend. */
  warnings?: string[] | null;
  /** Failure detail when `status === 'failed'`. */
  error?: string | null;
}

/** Verification states of a relationship. */
export type VerificationStatus = 'verified' | 'disputed' | 'unsupported' | 'unverified';

/** A node of the knowledge graph (one extracted entity). */
export interface GraphNode {
  id: string;
  label: string;
  /** Entity category (person, place, event, date, ...). */
  type?: string | null;
  /** Optional layout hint; when present the client places the node there. */
  position?: { x: number; y: number } | null;
}

/** A relationship edge of the knowledge graph. */
export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  /** Relationship description shown on the edge label. */
  label?: string | null;
  verificationStatus?: VerificationStatus | string | null;
}

/** Payload of GET /api/analysis/{id}/graph. */
export interface GraphResponse {
  analysisId: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

/** One piece of supporting evidence. */
export interface EvidenceItem {
  id: string;
  /** Where the evidence comes from (archive, record, transcription, ...). */
  source: string;
  /** Short quotation or description supporting the relationship. */
  excerpt?: string | null;
  /** Optional link to the source material. */
  url?: string | null;
}

/** Payload of GET /api/analysis/{id}/relationships/{relId}/evidence. */
export interface EvidenceResponse {
  /** The relationship this evidence supports. */
  relationshipId: string;
  verificationStatus: VerificationStatus | string;
  summary?: string | null;
  evidence: EvidenceItem[];
  /** Extra uncertainty notes from the backend. */
  notes?: string[] | null;
}

/** One piece of evidence behind a text-search graph edge. */
export interface GraphEvidence {
  source: string;
  excerpt?: string | null;
  url?: string | null;
}

/** A text-search graph edge, with the records that support it. */
export interface SearchGraphEdge extends GraphEdge {
  evidence: GraphEvidence[];
}

/** Payload of GET /api/search/graph?query=... */
export interface SearchGraphResponse {
  query: string;
  nodes: GraphNode[];
  edges: SearchGraphEdge[];
  sources: GraphEvidence[];
  /** Honest notes about thin or partial relationship data. */
  limitations: string[];
  notice?: string | null;
}

/** Payload of GET /api/search?query=... (A5 text search). */
export interface SearchResponse {
  /** The query the backend actually ran (normalised). */
  query: string;
  /** Matching historical records; empty when nothing matched. */
  results: HistoricalMatch[];
  /** Grounded explanation built from those records; null when nothing matched. */
  briefing?: HistoricalBriefing | null;
  /**
   * Present when the answer may be thin for a reason other than "nothing
   * matched" — today, the live source could not be reached or was
   * rate-limited. Lets the UI say so instead of implying the archive is
   * empty.
   */
  notice?: string | null;
}
