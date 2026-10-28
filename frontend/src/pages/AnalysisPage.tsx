import { useCallback, useEffect, useRef, useState } from 'react';

import { AnalysisStatus } from '../components/analysis/AnalysisStatus';
import { ExtractedEntities } from '../components/analysis/ExtractedEntities';
import { EvidencePanel } from '../components/evidence/EvidencePanel';
import { KnowledgeGraph } from '../components/graph/KnowledgeGraph';
import { SubjectHeader } from '../components/subject/SubjectHeader';
import { revealSubjectSection, useSubjectRoute } from '../hooks/useSubjectRoute';
import {
  ApiError,
  getAnalysis,
  getAnalysisGraph,
  getRelationshipEvidence,
  type AnalysisResponse,
  type EvidenceResponse,
  type GraphEdge,
  type GraphNode,
} from '../services/api';

interface AnalysisPageProps {
  /** Id of the analysis to load and render; null/undefined shows the empty state. */
  analysisId?: string | null;
}

type PageState =
  | { phase: 'empty' }
  | { phase: 'loading' }
  | { phase: 'error'; message: string }
  | { phase: 'ready'; data: AnalysisResponse };

type GraphState =
  | { phase: 'idle' }
  | { phase: 'loading' }
  | { phase: 'error'; message: string }
  | { phase: 'ready'; nodes: GraphNode[]; edges: GraphEdge[] };

type EvidenceState =
  | { phase: 'idle' }
  | { phase: 'loading' }
  | { phase: 'error'; message: string }
  | { phase: 'ready'; data: EvidenceResponse };

function toErrorMessage(err: unknown): string {
  return err instanceof ApiError
    ? err.message
    : 'Something went wrong while loading the analysis. Please try again.';
}

/** How often a queued/processing run is re-polled (B2 contract). */
const POLL_INTERVAL_MS = 750;
/**
 * Cap on consecutive polls (~3 minutes): a run the server never finishes
 * is reported as an error instead of showing "Processing" forever.
 */
const POLL_MAX_ATTEMPTS = 240;

/**
 * A3 — the analysis results page.
 *
 * Takes an analysis id (e.g. from a completed upload) and loads the result
 * via the service layer. Renders a useful empty state before any analysis
 * exists, a loading state, an error state with retry, and — on success —
 * the status panel plus extracted text, entities and historical matches,
 * all driven by the API response.
 */
export function AnalysisPage({ analysisId }: AnalysisPageProps) {
  const [state, setState] = useState<PageState>(
    analysisId ? { phase: 'loading' } : { phase: 'empty' },
  );
  const [reloadKey, setReloadKey] = useState(0);
  const [graphState, setGraphState] = useState<GraphState>({ phase: 'idle' });
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [evidenceState, setEvidenceState] = useState<EvidenceState>({ phase: 'idle' });
  const pollAttemptsRef = useRef(0);
  const { open } = useSubjectRoute();

  const retry = useCallback(() => setReloadKey((key) => key + 1), []);

  /**
   * Open one of the briefing's related topics: record it in the subject
   * route (which loads it in the search section, the same view a searched
   * subject gets) and reveal that section, because it renders further up
   * the page than the analysis results.
   */
  const openTopic = useCallback(
    (query: string) => {
      open(query);
      revealSubjectSection();
    },
    [open],
  );

  useEffect(() => {
    if (!analysisId) {
      setState({ phase: 'empty' });
      return;
    }

    let cancelled = false;
    setState({ phase: 'loading' });

    getAnalysis(analysisId).then(
      (data) => {
        if (!cancelled) setState({ phase: 'ready', data });
      },
      (err: unknown) => {
        if (!cancelled) setState({ phase: 'error', message: toErrorMessage(err) });
      },
    );

    return () => {
      cancelled = true;
    };
  }, [analysisId, reloadKey]);

  // A new analysis starts with a clean selection.
  useEffect(() => {
    setSelectedEdgeId(null);
    setGraphState({ phase: 'idle' });
    setEvidenceState({ phase: 'idle' });
    pollAttemptsRef.current = 0;
  }, [analysisId]);

  // B2 contract: after a 202, poll GET /api/analysis/{id} until the run
  // reaches a terminal state. Each refresh replaces `state`, which
  // re-arms this effect — so the loop stops by itself on 'completed' or
  // 'failed', on any poll error (rendered as the error state), and after
  // POLL_MAX_ATTEMPTS. Results stay visible while polling; there is no
  // loading flicker between polls.
  useEffect(() => {
    if (state.phase !== 'ready') {
      pollAttemptsRef.current = 0;
      return;
    }
    const status = state.data.status;
    if (status !== 'queued' && status !== 'processing') {
      pollAttemptsRef.current = 0;
      return;
    }
    if (!analysisId) return;
    if (pollAttemptsRef.current >= POLL_MAX_ATTEMPTS) {
      setState({
        phase: 'error',
        message:
          'The analysis is still running after several minutes. Please try again shortly.',
      });
      return;
    }
    pollAttemptsRef.current += 1;

    let cancelled = false;
    const timer = window.setTimeout(() => {
      if (cancelled) return;
      getAnalysis(analysisId).then(
        (data) => {
          if (!cancelled) setState({ phase: 'ready', data });
        },
        (err: unknown) => {
          if (!cancelled) setState({ phase: 'error', message: toErrorMessage(err) });
        },
      );
    }, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [state, analysisId]);

  // Load the knowledge graph once the analysis result is ready, and
  // refresh it whenever the run advances (a graph read during
  // 'processing' can predate the persisted relationships).
  const analysisStatus = state.phase === 'ready' ? state.data.status : null;

  useEffect(() => {
    if (state.phase !== 'ready' || !analysisId) return;

    let cancelled = false;
    setGraphState({ phase: 'loading' });

    getAnalysisGraph(analysisId).then(
      (graph) => {
        if (!cancelled) {
          setGraphState({ phase: 'ready', nodes: graph.nodes, edges: graph.edges });
        }
      },
      (err: unknown) => {
        if (!cancelled) setGraphState({ phase: 'error', message: toErrorMessage(err) });
      },
    );

    return () => {
      cancelled = true;
    };
  }, [analysisId, state.phase, analysisStatus, reloadKey]);

  // Load the evidence behind the selected relationship. The cancelled flag
  // keeps a slow response from overwriting a newer selection.
  useEffect(() => {
    if (state.phase !== 'ready' || !analysisId || !selectedEdgeId) {
      setEvidenceState({ phase: 'idle' });
      return;
    }

    let cancelled = false;
    setEvidenceState({ phase: 'loading' });

    getRelationshipEvidence(analysisId, selectedEdgeId).then(
      (data) => {
        if (!cancelled) setEvidenceState({ phase: 'ready', data });
      },
      (err: unknown) => {
        if (!cancelled) setEvidenceState({ phase: 'error', message: toErrorMessage(err) });
      },
    );

    return () => {
      cancelled = true;
    };
  }, [analysisId, selectedEdgeId, state.phase]);

  if (state.phase === 'empty') {
    return (
      <section
        aria-label="Analysis results"
        className="rounded-xl border border-dashed border-ivory-300 bg-ivory-50 p-6 text-center"
      >
        <h2 className="font-display text-lg text-teal-800">No analysis yet</h2>
        <p className="mx-auto mt-1 max-w-md text-sm text-ink-500">
          Upload an image above and its extracted text, entities and historical
          matches will appear here.
        </p>
      </section>
    );
  }

  if (state.phase === 'loading') {
    return (
      <section aria-label="Analysis results" aria-busy="true">
        <p role="status" className="rounded-xl border border-ivory-300 bg-ivory p-5 text-sm text-ink-700 shadow-sm">
          Loading analysis results…
        </p>
      </section>
    );
  }

  if (state.phase === 'error') {
    return (
      <section aria-label="Analysis results" className="rounded-xl border border-terracotta-300 bg-terracotta-50 p-5 shadow-sm">
        <div role="alert" className="text-sm text-terracotta-700">
          <span className="font-display text-lg font-medium text-terracotta-800">
            Could not load the analysis.
          </span>{' '}
          {state.message}
        </div>
        <button
          type="button"
          onClick={retry}
          className="mt-3 rounded-lg bg-teal-600 px-3 py-2 text-sm font-medium text-ivory transition-colors hover:bg-teal-700"
        >
          Try again
        </button>
      </section>
    );
  }

  const { data } = state;

  return (
    <section aria-label="Analysis results">
      <AnalysisStatus
        status={data.status}
        progress={data.progress}
        warnings={data.warnings ?? []}
        error={data.error}
      />

      {/* What this image is about: the subject the historical explanation
          belongs to, with its photograph. Labelled a *candidate*, because
          the identification is the vision model's reading, not a verified
          historical claim. */}
      {data.briefing ? (
        <SubjectHeader
          subject={data.briefing.subject ?? null}
          query={data.briefing.query}
          eyebrow="Candidate subject — from this image"
          className="mt-6"
        />
      ) : null}

      <ExtractedEntities
        extractedText={data.extractedText}
        interpretation={data.interpretation}
        entities={data.entities ?? []}
        matches={data.matches ?? []}
        briefing={data.briefing ?? null}
        onOpenTopic={openTopic}
      />

      <div className="mt-6 rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm">
        <h2 className="font-display text-lg text-teal-800">Relationship graph</h2>
        <div className="mt-3">
          {graphState.phase === 'loading' ? (
            <p role="status" className="text-sm text-ink-700">
              Loading graph…
            </p>
          ) : null}

          {graphState.phase === 'error' ? (
            <p role="alert" className="text-sm text-terracotta-700">
              {graphState.message}
            </p>
          ) : null}

          {graphState.phase === 'ready' ? (
            <KnowledgeGraph
              nodes={graphState.nodes}
              edges={graphState.edges}
              selectedEdgeId={selectedEdgeId}
              onSelectEdge={setSelectedEdgeId}
            />
          ) : null}
        </div>
      </div>

      {selectedEdgeId && evidenceState.phase !== 'idle' ? (
        <div className="mt-6">
          <EvidencePanel
            relationshipId={selectedEdgeId}
            loading={evidenceState.phase === 'loading'}
            error={evidenceState.phase === 'error' ? evidenceState.message : null}
            verificationStatus={
              evidenceState.phase === 'ready' ? evidenceState.data.verificationStatus : null
            }
            summary={evidenceState.phase === 'ready' ? evidenceState.data.summary : null}
            items={evidenceState.phase === 'ready' ? evidenceState.data.evidence : []}
            notes={evidenceState.phase === 'ready' ? evidenceState.data.notes ?? [] : []}
            onClose={() => setSelectedEdgeId(null)}
          />
        </div>
      ) : null}
    </section>
  );
}

export default AnalysisPage;
