import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import type { Core } from 'cytoscape';

import {
  GRAPH_STYLESHEET,
  KnowledgeGraph,
  toCytoscapeElements,
  verificationFor,
} from '../src/components/graph/KnowledgeGraph';
import { EvidencePanel } from '../src/components/evidence/EvidencePanel';

const NODES = [
  { id: 'n1', label: 'Ada Lovelace', type: 'person' },
  { id: 'n2', label: 'Analytical Engine', type: 'concept' },
];

const EDGES = [
  {
    id: 'rel-verified',
    source: 'n1',
    target: 'n2',
    label: 'wrote notes on',
    verificationStatus: 'verified',
  },
  {
    id: 'rel-unsupported',
    source: 'n1',
    target: 'n2',
    label: 'financed',
    verificationStatus: 'unsupported',
  },
];

function renderGraph(overrides: Partial<Parameters<typeof KnowledgeGraph>[0]> = {}) {
  // Cytoscape instance handed over through onReady (assigned via a mutable
  // holder to keep TypeScript's narrowing out of the way).
  const captured: { cy: Core | null } = { cy: null };
  render(
    <KnowledgeGraph
      nodes={NODES}
      edges={EDGES}
      onReady={(cy) => {
        captured.cy = cy;
      }}
      {...overrides}
    />,
  );
  return captured;
}

describe('KnowledgeGraph', () => {
  it('converts nodes and edges into Cytoscape elements, dropping broken edges', () => {
    const elements = toCytoscapeElements(NODES, [
      ...EDGES,
      { id: 'rel-broken', source: 'n1', target: 'missing-node', label: 'broken' },
    ]);

    const nodeElements = elements.filter((element) => element.group === 'nodes');
    const edgeElements = elements.filter((element) => element.group === 'edges');

    expect(nodeElements).toHaveLength(2);
    expect(nodeElements[0].data).toMatchObject({ id: 'n1', label: 'Ada Lovelace' });
    expect(edgeElements.map((element) => element.data.id)).toEqual([
      'rel-verified',
      'rel-unsupported',
    ]);
    // The verification status rides on the edge data, which is what the
    // stylesheet selectors match on.
    expect(edgeElements[1].data).toMatchObject({
      id: 'rel-unsupported',
      verificationStatus: 'unsupported',
    });
  });

  it('renders the graph with nodes, edges and readable labels', () => {
    const captured = renderGraph();

    expect(screen.getByLabelText('Knowledge graph')).toBeInTheDocument();
    const cy = captured.cy!;
    expect(cy.nodes()).toHaveLength(2);
    expect(cy.edges()).toHaveLength(2);
    expect(cy.getElementById('n1').data('label')).toBe('Ada Lovelace');
    expect(cy.getElementById('rel-verified').data('label')).toBe('wrote notes on');
  });

  it('reports edge and node selection through the callbacks', () => {
    const onSelectEdge = vi.fn();
    const onSelectNode = vi.fn();
    const captured = renderGraph({ onSelectEdge, onSelectNode });

    act(() => {
      captured.cy!.getElementById('rel-unsupported').emit('tap');
    });
    expect(onSelectEdge).toHaveBeenCalledWith('rel-unsupported');

    act(() => {
      captured.cy!.getElementById('n1').emit('tap');
    });
    expect(onSelectNode).toHaveBeenCalledWith('n1');
  });

  it('shows an empty state and skips Cytoscape when no relationships exist', () => {
    const onReady = vi.fn();
    render(<KnowledgeGraph nodes={NODES} edges={[]} onReady={onReady} />);

    expect(screen.getByText('No relationships to display yet.')).toBeInTheDocument();
    expect(screen.queryByLabelText('Knowledge graph')).not.toBeInTheDocument();
    expect(onReady).not.toHaveBeenCalled();
  });

  it('styles unsupported relationships distinctly from verified ones', () => {
    // Cytoscape's headless build exposes no computed style, so the rules
    // that the renderer applies are asserted on the stylesheet itself.
    const ruleFor = (status: string) =>
      GRAPH_STYLESHEET.find(
        (sheet) => sheet.selector === `edge[verificationStatus = "${status}"]`,
      );

    const unsupported = ruleFor('unsupported')?.style;
    const verified = ruleFor('verified')?.style;

    expect(unsupported?.['line-style']).toBe('dashed');
    expect(verified?.['line-style']).toBe('solid');
    expect(unsupported?.['line-color']).not.toBe(verified?.['line-color']);
  });

  it('falls back to the unverified look for unknown statuses', () => {
    expect(verificationFor('mystery').lineStyle).toBe('dotted');
    expect(verificationFor(undefined).label).toBe('Unverified');
  });

  it('keeps the controlled selection when the graph data changes', () => {
    const captured: { cy: Core | null } = { cy: null };
    const onReady = (cy: Core) => {
      captured.cy = cy;
    };
    const { rerender } = render(
      <KnowledgeGraph nodes={NODES} edges={EDGES} selectedEdgeId="rel-unsupported" onReady={onReady} />,
    );
    expect(captured.cy!.getElementById('rel-unsupported').selected()).toBe(true);

    // New array identities (same content) rebuild the graph instance; the
    // controlled selection must survive the rebuild.
    rerender(
      <KnowledgeGraph nodes={[...NODES]} edges={[...EDGES]} selectedEdgeId="rel-unsupported" onReady={onReady} />,
    );
    expect(captured.cy!.getElementById('rel-unsupported').selected()).toBe(true);
    expect(captured.cy!.getElementById('rel-verified').selected()).toBe(false);
  });
});

describe('EvidencePanel', () => {
  it('displays the evidence for the shown relationship', () => {
    render(
      <EvidencePanel
        relationshipId="rel-unsupported"
        verificationStatus="unsupported"
        summary="Two independent parish records mention the gift."
        items={[
          {
            id: 'ev1',
            source: 'Parish ledger, 1843',
            excerpt: '…a gift of £50 from A. Lovelace…',
            url: 'https://example.org/ledger',
          },
          { id: 'ev2', source: 'Family correspondence' },
        ]}
        notes={['The ledger ink is faded in places.']}
      />,
    );

    expect(screen.getByTestId('evidence-relationship-id')).toHaveTextContent('rel-unsupported');
    expect(screen.getByText('Unsupported')).toBeInTheDocument();
    expect(
      screen.getByText('Two independent parish records mention the gift.'),
    ).toBeInTheDocument();
    expect(screen.getByText('Parish ledger, 1843')).toBeInTheDocument();
    expect(screen.getByText('…a gift of £50 from A. Lovelace…')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open source' })).toHaveAttribute(
      'href',
      'https://example.org/ledger',
    );
    expect(screen.getByText('Family correspondence')).toBeInTheDocument();
    expect(screen.getByText('The ledger ink is faded in places.')).toBeInTheDocument();
  });

  it('renders its loading and error states', () => {
    const { rerender } = render(<EvidencePanel relationshipId="rel-1" loading />);
    expect(screen.getByText('Loading evidence…')).toBeInTheDocument();
    // No backend status yet → no verification badge is shown at all.
    expect(screen.queryByTestId('evidence-verification-status')).not.toBeInTheDocument();

    rerender(<EvidencePanel relationshipId="rel-1" error="backend offline" />);
    expect(screen.getByText(/backend offline/)).toBeInTheDocument();
    expect(screen.queryByText('Loading evidence…')).not.toBeInTheDocument();
  });
});

/* Acceptance: selecting an edge opens the evidence for the correct
   relationship ID — same wiring as AnalysisPage (selection state feeds the
   panel's relationship id). */
const EVIDENCE_BY_ID: Record<
  string,
  { verificationStatus: string; summary: string; items: { id: string; source: string }[] }
> = {
  'rel-verified': {
    verificationStatus: 'verified',
    summary: 'Verified by the Royal Society register.',
    items: [{ id: 'v1', source: 'Royal Society register' }],
  },
  'rel-unsupported': {
    verificationStatus: 'unsupported',
    summary: 'Only one secondary source mentions this.',
    items: [{ id: 'u1', source: 'Secondary biography, 1920' }],
  },
};

function GraphWithEvidence({ onReady }: { onReady: (cy: Core) => void }) {
  const [selected, setSelected] = useState<string | null>(null);
  const evidence = selected ? EVIDENCE_BY_ID[selected] : null;

  return (
    <div>
      <KnowledgeGraph
        nodes={NODES}
        edges={EDGES}
        selectedEdgeId={selected}
        onSelectEdge={setSelected}
        onReady={onReady}
      />
      {selected && evidence ? (
        <EvidencePanel
          relationshipId={selected}
          verificationStatus={evidence.verificationStatus}
          summary={evidence.summary}
          items={evidence.items}
        />
      ) : null}
    </div>
  );
}

describe('acceptance', () => {
  it('selecting an edge opens the evidence for the correct relationship ID', () => {
    const captured: { cy: Core | null } = { cy: null };
    render(<GraphWithEvidence onReady={(cy) => (captured.cy = cy)} />);

    expect(screen.queryByTestId('evidence-relationship-id')).not.toBeInTheDocument();

    act(() => {
      captured.cy!.getElementById('rel-unsupported').emit('tap');
    });

    expect(screen.getByTestId('evidence-relationship-id')).toHaveTextContent('rel-unsupported');
    expect(screen.getByText('Only one secondary source mentions this.')).toBeInTheDocument();
    expect(screen.queryByText('Verified by the Royal Society register.')).not.toBeInTheDocument();

    // Switching the selection shows the other relationship's evidence.
    act(() => {
      captured.cy!.getElementById('rel-verified').emit('tap');
    });

    expect(screen.getByTestId('evidence-relationship-id')).toHaveTextContent('rel-verified');
    expect(screen.getByText('Verified by the Royal Society register.')).toBeInTheDocument();
  });
});
