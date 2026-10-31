import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { ResearchPage } from '../src/pages/ResearchPage';
import type { ResearchCollectionDetail } from '../src/types/archive';

const listCollections = vi.fn();
const getCollection = vi.fn();
const listArchives = vi.fn();
const createCollection = vi.fn();
const renameCollection = vi.fn();
const deleteCollection = vi.fn();
const addCollectionItem = vi.fn();
const removeCollectionItem = vi.fn();
const createNote = vi.fn();
const deleteNote = vi.fn();

vi.mock('../src/services/archiveApi', () => ({
  addCollectionItem: (...args: unknown[]) => addCollectionItem(...args),
  collectionReportFilename: (name: string) => `${name}-report.pdf`,
  collectionReportUrl: (id: string) => `/api/collections/${id}/report.pdf`,
  createCollection: (...args: unknown[]) => createCollection(...args),
  createNote: (...args: unknown[]) => createNote(...args),
  deleteCollection: (...args: unknown[]) => deleteCollection(...args),
  deleteNote: (...args: unknown[]) => deleteNote(...args),
  getCollection: (...args: unknown[]) => getCollection(...args),
  listArchives: (...args: unknown[]) => listArchives(...args),
  listCollections: (...args: unknown[]) => listCollections(...args),
  removeCollectionItem: (...args: unknown[]) => removeCollectionItem(...args),
  renameCollection: (...args: unknown[]) => renameCollection(...args),
}));

vi.mock('../src/services/accountApi', () => ({
  ApiError: class ApiError extends Error {},
  downloadPdf: vi.fn(),
}));

const DETAIL: ResearchCollectionDetail = {
  id: 'c1',
  name: 'Gujarat Stepwell Research',
  description: 'Field notes and survey documents',
  itemCount: 1,
  noteCount: 1,
  createdAt: '2026-10-29T10:00:00+00:00',
  updatedAt: '2026-10-29T10:00:00+00:00',
  items: [
    {
      type: 'archive',
      id: 'a1',
      name: 'Gujarat Stepwell Heritage Collection',
      addedAt: '2026-10-29T10:00:00+00:00',
    },
  ],
  notes: [
    {
      id: 'n1',
      body: 'Adalaj and Rani ki Vav share the same seven-storey plan.',
      subjectQuery: 'Rani ki Vav',
      createdAt: '2026-10-29T10:00:00+00:00',
      updatedAt: '2026-10-29T10:00:00+00:00',
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  listCollections.mockResolvedValue([]);
  listArchives.mockResolvedValue([]);
});

describe('Research workspace', () => {
  it('shows an empty state before any collection exists', async () => {
    render(<ResearchPage />);

    expect(await screen.findByText('No collections yet.')).toBeInTheDocument();
    expect(screen.getByText('Select or create a collection')).toBeInTheDocument();
  });

  it('opens a collection with its materials and notes', async () => {
    listCollections.mockResolvedValue([DETAIL]);
    getCollection.mockResolvedValue(DETAIL);

    render(<ResearchPage />);

    fireEvent.click(
      await screen.findByRole('button', { name: /Gujarat Stepwell Research/ }),
    );

    expect(await screen.findByText('Research notes')).toBeInTheDocument();
    expect(
      screen.getByText('Adalaj and Rani ki Vav share the same seven-storey plan.'),
    ).toBeInTheDocument();
    expect(screen.getAllByText('Rani ki Vav').length).toBeGreaterThan(0);
    expect(screen.getByText(/1 archive\(s\) · 1 note\(s\)/)).toBeInTheDocument();
  });

  it('reports a collection load failure without inventing content', async () => {
    listCollections.mockResolvedValue([DETAIL]);
    getCollection.mockRejectedValue(new Error('gone'));

    render(<ResearchPage />);

    fireEvent.click(
      await screen.findByRole('button', { name: /Gujarat Stepwell Research/ }),
    );

    expect(await screen.findByText('Could not load the collection.')).toBeInTheDocument();
    expect(
      screen.queryByText('Adalaj and Rani ki Vav share the same seven-storey plan.'),
    ).not.toBeInTheDocument();
  });
});
