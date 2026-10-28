import { useEffect, useRef } from 'react';
import cytoscape from 'cytoscape';
import type { Core, ElementDefinition } from 'cytoscape';

/** A node to display. Primitive props only — no API contract types. */
export interface GraphNodeView {
  id: string;
  label: string;
  type?: string | null;
  /** Optional layout hint; when present the node is placed there. */
  position?: { x: number; y: number } | null;
}

/** A relationship edge to display. */
export interface GraphEdgeView {
  id: string;
  source: string;
  target: string;
  label?: string | null;
  verificationStatus?: string | null;
}

interface KnowledgeGraphProps {
  nodes: GraphNodeView[];
  edges: GraphEdgeView[];
  /** Currently selected relationship (controlled by the parent). */
  selectedEdgeId?: string | null;
  /** Called with the tapped edge id, or null when the background is tapped. */
  onSelectEdge?: (edgeId: string | null) => void;
  /** Called with the tapped node id. */
  onSelectNode?: (nodeId: string) => void;
  /** Receives the cytoscape instance once it is created (tests/integrations). */
  onReady?: (cy: Core) => void;
}

/**
 * Presentation of the provisional verification statuses. Shared with the
 * evidence panel so a status always looks the same wherever it appears.
 */
export const VERIFICATION_PRESENTATION: Record<
  string,
  { label: string; color: string; lineStyle: 'solid' | 'dashed' | 'dotted' }
> = {
  verified: { label: 'Verified', color: '#146a66', lineStyle: 'solid' },
  disputed: { label: 'Disputed', color: '#c1623c', lineStyle: 'solid' },
  unsupported: { label: 'Unsupported', color: '#934027', lineStyle: 'dashed' },
  unverified: { label: 'Unverified', color: '#a4926a', lineStyle: 'dotted' },
};

/** Falls back to the "unverified" look for unknown statuses. */
export function verificationFor(status: string | null | undefined) {
  if (status && VERIFICATION_PRESENTATION[status]) return VERIFICATION_PRESENTATION[status];
  return VERIFICATION_PRESENTATION.unverified;
}

/**
 * Converts graph nodes and edges into Cytoscape elements. Edges that
 * reference a missing node are dropped — Cytoscape throws on them and a
 * broken backend payload must not take the page down.
 */
export function toCytoscapeElements(
  nodes: GraphNodeView[],
  edges: GraphEdgeView[],
): ElementDefinition[] {
  const nodeIds = new Set(nodes.map((node) => node.id));

  const nodeElements: ElementDefinition[] = nodes.map((node) => ({
    group: 'nodes',
    data: { id: node.id, label: node.label, entityType: node.type ?? '' },
    ...(node.position ? { position: { x: node.position.x, y: node.position.y } } : {}),
  }));

  const edgeElements: ElementDefinition[] = edges
    .filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target))
    .map((edge) => ({
      group: 'edges',
      data: {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        label: edge.label ?? '',
        verificationStatus: edge.verificationStatus ?? 'unverified',
      },
    }));

  return [...nodeElements, ...edgeElements];
}

/** One stylesheet rule in Cytoscape's JSON form. */
export interface GraphStylesheetRule {
  selector: string;
  style: Record<string, string | number>;
}

/**
 * The stylesheet handed to Cytoscape. Exported so tests can assert the
 * verification-status rules directly (Cytoscape's headless test builds
 * have no style module to query computed values from).
 */
export const GRAPH_STYLESHEET: GraphStylesheetRule[] = [
  {
    selector: 'node',
    style: {
      'label': 'data(label)',
      'color': '#1f2a28',
      'font-size': 12,
      'text-valign': 'bottom',
      'text-halign': 'center',
      'text-margin-y': 8,
      'text-wrap': 'wrap',
      'text-max-width': 120,
      'text-background-color': '#fffff0',
      'text-background-opacity': 0.85,
      'text-background-padding': 2,
      'background-color': '#fffff0',
      'border-width': 2,
      'border-color': '#146a66',
      'width': 36,
      'height': 36,
    },
  },
  {
    selector: 'node:selected',
    style: {
      'border-width': 4,
      'border-color': '#b1502e',
      'background-color': '#f7f1e1',
    },
  },
  {
    selector: 'edge',
    style: {
      'width': 2,
      'line-color': '#a4926a',
      'target-arrow-color': '#a4926a',
      'target-arrow-shape': 'triangle',
      'arrow-scale': 0.9,
      'curve-style': 'bezier',
      'label': 'data(label)',
      'font-size': 10,
      'color': '#5b6462',
      'text-rotation': 'autorotate',
      'text-background-color': '#fffff0',
      'text-background-opacity': 0.85,
      'text-background-padding': 2,
    },
  },
  ...Object.entries(VERIFICATION_PRESENTATION).map(([status, presentation]) => ({
    selector: `edge[verificationStatus = "${status}"]`,
    style: {
      'line-color': presentation.color,
      'target-arrow-color': presentation.color,
      'line-style': presentation.lineStyle,
    },
  })),
  {
    selector: 'edge:selected',
    style: {
      'width': 4,
    },
  },
];

/**
 * A4 — interactive knowledge graph rendered with Cytoscape.js.
 *
 * Nodes and edges arrive as plain view props, are converted into Cytoscape
 * elements, and are laid out with a deterministic grid (or preset positions
 * when every node carries one). Zoom and pan use Cytoscape's built-in
 * controls; tapping an edge or a node reports the id through the selection
 * callbacks. Verification statuses are visually distinguished per the
 * legend. When no relationships exist the canvas is replaced by an empty
 * state instead of an empty graph.
 */
export function KnowledgeGraph({
  nodes,
  edges,
  selectedEdgeId,
  onSelectEdge,
  onSelectNode,
  onReady,
}: KnowledgeGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const handlersRef = useRef({ onSelectEdge, onSelectNode, onReady });
  handlersRef.current = { onSelectEdge, onSelectNode, onReady };
  // Mirrors the controlled selection so a graph rebuild (new data) can
  // re-apply it to the fresh instance.
  const selectedEdgeIdRef = useRef(selectedEdgeId ?? null);

  const hasRelationships = edges.length > 0;

  useEffect(() => {
    const container = containerRef.current;
    if (!hasRelationships || !container) return;

    const elements = toCytoscapeElements(nodes, edges);
    const allPositioned =
      nodes.length > 0 && nodes.every((node) => node.position != null);

    // jsdom (component tests) has no canvas renderer; run headless there.
    const headless = import.meta.env.MODE === 'test';

    const cy = cytoscape({
      ...(headless ? { headless: true } : { container }),
      elements,
      style: GRAPH_STYLESHEET as unknown as cytoscape.CytoscapeOptions['style'],
      layout: { name: allPositioned ? 'preset' : 'grid' },
      zoom: 1,
      pan: { x: 0, y: 0 },
      minZoom: 0.3,
      maxZoom: 3,
      zoomingEnabled: true,
      panningEnabled: true,
      userZoomingEnabled: true,
      userPanningEnabled: true,
    });
    cyRef.current = cy;

    cy.on('tap', 'edge', (event) => {
      const id = event.target.id();
      cy.elements().unselect();
      event.target.select();
      handlersRef.current.onSelectEdge?.(id);
    });

    cy.on('tap', 'node', (event) => {
      handlersRef.current.onSelectNode?.(event.target.id());
    });

    // Tapping the background clears the selection.
    cy.on('tap', (event) => {
      if (event.target === cy) {
        cy.elements().unselect();
        handlersRef.current.onSelectEdge?.(null);
      }
    });

    const resizeObserver =
      typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => cy.resize()) : null;
    resizeObserver?.observe(container);

    // Re-apply the controlled selection to the new instance.
    if (selectedEdgeIdRef.current) cy.$id(selectedEdgeIdRef.current).select();

    handlersRef.current.onReady?.(cy);

    return () => {
      resizeObserver?.disconnect();
      cy.destroy();
      cyRef.current = null;
    };
    // The graph is rebuilt when the data changes; results are static per analysis.
  }, [nodes, edges, hasRelationships]);

  // Keep the controlled selection in sync (and the mirror current).
  useEffect(() => {
    selectedEdgeIdRef.current = selectedEdgeId ?? null;
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().unselect();
    if (selectedEdgeId) cy.$id(selectedEdgeId).select();
  }, [selectedEdgeId, hasRelationships]);

  if (!hasRelationships) {
    return (
      <div
        role="status"
        className="rounded-lg border border-dashed border-ivory-300 bg-ivory-50 px-4 py-8 text-center"
      >
        <p className="font-display text-teal-800">No relationships to display yet.</p>
        <p className="mx-auto mt-1 max-w-md text-sm text-ink-500">
          Once the analysis links its entities, their relationships will appear here as an
          interactive graph.
        </p>
      </div>
    );
  }

  return (
    <div>
      <div
        ref={containerRef}
        aria-label="Knowledge graph"
        className="h-96 w-full rounded-lg border border-ivory-300 bg-ivory-50"
      />
      <ul className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-500">
        {Object.entries(VERIFICATION_PRESENTATION).map(([status, presentation]) => (
          <li key={status} className="flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="inline-block w-6 border-t-2"
              style={{ borderColor: presentation.color, borderStyle: presentation.lineStyle }}
            />
            {presentation.label}
          </li>
        ))}
      </ul>
    </div>
  );
}

export default KnowledgeGraph;
