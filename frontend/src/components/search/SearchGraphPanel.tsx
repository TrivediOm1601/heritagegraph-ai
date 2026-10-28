import { useEffect, useMemo, useState } from 'react';

import { KnowledgeGraph, verificationFor } from '../graph/KnowledgeGraph';
import { ApiError, searchGraph } from '../../services/accountApi';
import type {
  GraphEvidence,
  SearchGraphEdge,
  SearchGraphResponse,
} from '../../types/api';

export type { SearchGraphResponse };

interface SearchGraphPanelProps {
  /** The subject the graph is for (the search the user ran). */
  query: string;
  /** Opens a related topic (the same interaction the subject page uses). */
  onOpenTopic?: (query: string) => void;
}

type GraphState =
  | { phase: 'loading' }
  | { phase: 'error'; message: string }
  | { phase: 'ready'; data: SearchGraphResponse };

/**
 * R7 — knowledge graph for a historical **text search**.
 *
 * Reuses the analysis graph component (`KnowledgeGraph`) and the same node /
 * edge contract: the backend supplies stable ids, labels, types and edges
 * that only exist where the retrieved records' own text supports them.
 *
 * The panel is deliberately explicit about uncertainty: whenever the backend
 * reports limited relationship data, those limitations are listed beneath the
 * canvas. Nothing is added to make the graph look fuller.
 */
export function SearchGraphPanel({ query, onOpenTopic }: SearchGraphPanelProps) {
  const [state, setState] = useState<GraphState>({ phase: 'loading' });
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setState({ phase: 'loading' });
    setSelectedEdgeId(null);
    setSelectedNodeId(null);

    searchGraph(query).then(
      (data) => {
        if (!cancelled) setState({ phase: 'ready', data });
      },
      (err: unknown) => {
        if (!cancelled) {
          setState({
            phase: 'error',
            message:
              err instanceof ApiError
                ? err.message
                : 'Could not build the knowledge graph. Please try again.',
          });
        }
      },
    );

    return () => {
      cancelled = true;
    };
  }, [query]);

  const data = state.phase === 'ready' ? state.data : null;

  const selectedNode = useMemo(
    () => (data && selectedNodeId ? data.nodes.find((node) => node.id === selectedNodeId) ?? null : null),
    [data, selectedNodeId],
  );

  const selectedEdge = useMemo(
    () => (data && selectedEdgeId ? data.edges.find((edge) => edge.id === selectedEdgeId) ?? null : null),
    [data, selectedEdgeId],
  );

  const nodeEdges = useMemo(() => {
    if (!data || !selectedNode) return [];
    return data.edges.filter(
      (edge) => edge.source === selectedNode.id || edge.target === selectedNode.id,
    );
  }, [data, selectedNode]);

  const labelOf = (nodeId: string) =>
    data?.nodes.find((node) => node.id === nodeId)?.label ?? nodeId;

  if (state.phase === 'loading') {
    return (
      <p role="status" className="text-sm text-ink-700">
        Building the knowledge graph for “{query}”…
      </p>
    );
  }

  if (state.phase === 'error') {
    return (
      <div
        role="alert"
        className="rounded-lg border border-terracotta-300 bg-terracotta-50 px-3 py-2 text-sm text-terracotta-700"
      >
        <span className="font-medium">Could not build the graph.</span> {state.message}
      </div>
    );
  }

  const { data: graph } = state;

  if (graph.nodes.length === 0) {
    return (
      <div
        role="status"
        className="rounded-lg border border-dashed border-ivory-300 bg-ivory-50 px-4 py-8 text-center"
      >
        <p className="font-display text-teal-800">No graph for this search</p>
        {graph.limitations.map((note) => (
          <p key={note} className="mx-auto mt-2 max-w-md text-sm text-ink-500">
            {note}
          </p>
        ))}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {graph.notice ? (
        <p
          role="status"
          className="rounded-lg border border-terracotta-200 bg-terracotta-50 px-3 py-2 text-xs text-terracotta-700"
        >
          {graph.notice}
        </p>
      ) : null}

      <KnowledgeGraph
        nodes={graph.nodes}
        edges={graph.edges}
        selectedEdgeId={selectedEdgeId}
        onSelectEdge={(edgeId) => {
          setSelectedEdgeId(edgeId);
          if (edgeId) setSelectedNodeId(null);
        }}
        onSelectNode={(nodeId) => {
          setSelectedNodeId(nodeId);
          setSelectedEdgeId(null);
        }}
      />

      <p className="text-xs text-ink-500">
        Edges are drawn only where the retrieved records support them. Select a node to
        see its connections, or an edge to read the evidence behind it.
      </p>

      {selectedNode ? (
        <section
          aria-label="Selected entity"
          className="rounded-lg border border-ivory-300 bg-ivory-50 p-4"
        >
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h4 className="font-display text-base text-teal-800">{selectedNode.label}</h4>
            <span className="rounded-full bg-ivory-200 px-2 py-0.5 text-xs text-ink-700">
              {selectedNode.type === 'record'
                ? 'Retrieved record'
                : selectedNode.id.startsWith('subject:')
                  ? 'Search subject'
                  : (selectedNode.type ?? 'entity')}
            </span>
          </div>
          {nodeEdges.length > 0 ? (
            <ul className="mt-2 list-inside list-disc text-sm text-ink-700">
              {nodeEdges.map((edge) => (
                <li key={edge.id}>
                  {edge.source === selectedNode.id
                    ? `${edge.label ?? 'related to'} → ${labelOf(edge.target)}`
                    : `${labelOf(edge.source)} → ${edge.label ?? 'related to'} this`}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-sm text-ink-500">
              No supported relationship is recorded for this entity.
            </p>
          )}
          {onOpenTopic && !selectedNode.id.startsWith('subject:') ? (
            <button
              type="button"
              onClick={() => onOpenTopic(selectedNode.label)}
              className="mt-3 rounded-lg border border-teal-600 px-3 py-1.5 text-sm font-medium text-teal-700 transition-colors hover:bg-teal-50"
            >
              Open “{selectedNode.label}” as a subject
            </button>
          ) : null}
        </section>
      ) : null}

      {selectedEdge ? (
        <section
          aria-label="Relationship evidence"
          className="rounded-lg border border-ivory-300 bg-ivory-50 p-4"
        >
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h4 className="font-display text-base text-teal-800">
              {labelOf(selectedEdge.source)} → {labelOf(selectedEdge.target)}
            </h4>
            <span
              className="rounded-full px-2 py-0.5 text-xs font-semibold text-ivory"
              style={{ backgroundColor: verificationFor(selectedEdge.verificationStatus).color }}
            >
              {verificationFor(selectedEdge.verificationStatus).label}
            </span>
          </div>
          {selectedEdge.label ? (
            <p className="mt-1 text-sm text-ink-700">{selectedEdge.label}</p>
          ) : null}
          <h5 className="mt-3 text-sm font-semibold text-ink-700">Supporting evidence</h5>
          {selectedEdge.evidence.length > 0 ? (
            <ul className="mt-1 flex flex-col gap-2">
              {selectedEdge.evidence.map((item: GraphEvidence, index: number) => (
                <li key={`${index}-${item.source}`} className="text-sm text-ink-700">
                  <span className="font-medium">{item.source}</span>
                  {item.excerpt ? <span className="block text-ink-500">“{item.excerpt}”</span> : null}
                  {item.url ? (
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="mt-0.5 inline-block text-xs text-teal-700 underline"
                    >
                      View source
                    </a>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-sm text-ink-500">
              No supporting evidence was recorded for this edge.
            </p>
          )}
        </section>
      ) : null}

      {graph.limitations.length > 0 ? (
        <div className="rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2">
          <h5 className="text-sm font-semibold text-ink-700">About this graph</h5>
          <ul className="mt-1 list-inside list-disc text-xs text-ink-500">
            {graph.limitations.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {graph.sources.length > 0 ? (
        <div>
          <h5 className="text-sm font-semibold text-ink-700">Records behind this graph</h5>
          <ul className="mt-1 flex flex-col gap-1 text-sm text-ink-700">
            {graph.sources.map((source, index) => (
              <li key={`${index}-${source.source}`}>
                {source.url ? (
                  <a
                    href={source.url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="text-teal-700 underline"
                  >
                    {source.source}
                  </a>
                ) : (
                  source.source
                )}
                {source.excerpt ? (
                  <span className="block text-xs text-ink-500">“{source.excerpt}”</span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export type { SearchGraphEdge };
export default SearchGraphPanel;
