import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { ArchivePage } from '../src/pages/ArchivePage';
import type { ArchiveDetail, ArchiveFileItem } from '../src/types/archive';

const listArchives = vi.fn();
const createArchive = vi.fn();
const getArchive = vi.fn();
const getArchiveFile = vi.fn();
const getArchiveTimeline = vi.fn();
const renameArchive = vi.fn();
const deleteArchive = vi.fn();
const deleteArchiveFile = vi.fn();
const reprocessArchiveFile = vi.fn();
const uploadArchiveFiles = vi.fn();
const searchArchive = vi.fn();
const listCollections = vi.fn();
const getCollection = vi.fn();
const createCollection = vi.fn();
const renameCollection = vi.fn();
const deleteCollection = vi.fn();
const addCollectionItem = vi.fn();
const removeCollectionItem = vi.fn();
const createNote = vi.fn();
const deleteNote = vi.fn();
const listHeritageSites = vi.fn();
const buildHeritageRoute = vi.fn();

vi.mock('../src/services/archiveApi', () => ({
  ARCHIVE_ACCEPT: '.pdf',
  MAX_FILES_PER_UPLOAD: 20,
  archiveFileDownloadUrl: (id: string) => `/api/archives/files/${id}/download`,
  archiveFilePreviewUrl: (id: string) => `/api/archives/files/${id}/preview`,
  archiveReportFilename: (name: string) => `${name}-report.pdf`,
  archiveReportUrl: (id: string) => `/api/archives/${id}/report.pdf`,
  collectionReportFilename: (name: string) => `${name}-report.pdf`,
  collectionReportUrl: (id: string) => `/api/collections/${id}/report.pdf`,
  addCollectionItem: (...args: unknown[]) => addCollectionItem(...args),
  buildHeritageRoute: (...args: unknown[]) => buildHeritageRoute(...args),
  createArchive: (...args: unknown[]) => createArchive(...args),
  createCollection: (...args: unknown[]) => createCollection(...args),
  createNote: (...args: unknown[]) => createNote(...args),
  deleteArchive: (...args: unknown[]) => deleteArchive(...args),
  deleteArchiveFile: (...args: unknown[]) => deleteArchiveFile(...args),
  deleteCollection: (...args: unknown[]) => deleteCollection(...args),
  deleteNote: (...args: unknown[]) => deleteNote(...args),
  getArchive: (...args: unknown[]) => getArchive(...args),
  getArchiveFile: (...args: unknown[]) => getArchiveFile(...args),
  getArchiveTimeline: (...args: unknown[]) => getArchiveTimeline(...args),
  getCollection: (...args: unknown[]) => getCollection(...args),
  listArchives: (...args: unknown[]) => listArchives(...args),
  listCollections: (...args: unknown[]) => listCollections(...args),
  listHeritageSites: (...args: unknown[]) => listHeritageSites(...args),
  removeCollectionItem: (...args: unknown[]) => removeCollectionItem(...args),
  renameArchive: (...args: unknown[]) => renameArchive(...args),
  renameCollection: (...args: unknown[]) => renameCollection(...args),
  reprocessArchiveFile: (...args: unknown[]) => reprocessArchiveFile(...args),
  searchArchive: (...args: unknown[]) => searchArchive(...args),
  uploadArchiveFiles: (...args: unknown[]) => uploadArchiveFiles(...args),
}));

vi.mock('../src/services/accountApi', () => ({
  ApiError: class ApiError extends Error {
    status?: number;
    constructor(message: string, status?: number) {
      super(message);
      this.status = status;
    }
  },
  downloadPdf: vi.fn(),
}));

function file(overrides: Partial<ArchiveFileItem> = {}): ArchiveFileItem {
  return {
    id: 'f1',
    archiveId: 'a1',
    filename: 'rani-ki-vav-survey.pdf',
    contentType: 'application/pdf',
    sizeBytes: 2048,
    kind: 'pdf',
    status: 'completed',
    statusDetail: '3 pages',
    error: null,
    language: 'en',
    languageScript: 'latin',
    pageCount: 3,
    pageLabel: 'page',
    duplicateOfId: null,
    duplicateKind: null,
    warnings: [],
    archivePath: null,
    createdAt: '2026-10-29T10:00:00+00:00',
    updatedAt: '2026-10-29T10:00:00+00:00',
    ...overrides,
  };
}

function detail(overrides: Partial<ArchiveDetail> = {}): ArchiveDetail {
  return {
    id: 'a1',
    name: 'Gujarat Stepwell Heritage Collection',
    description: 'Survey documents',
    region: 'Gujarat',
    tags: ['stepwell'],
    createdAt: '2026-10-29T10:00:00+00:00',
    updatedAt: '2026-10-29T10:00:00+00:00',
    stats: {
      files: 2,
      completed: 1,
      partial: 0,
      failed: 1,
      processing: 0,
      pages: 3,
      characters: 1200,
      entities: 4,
      timelineEvents: 1,
      duplicates: 0,
      byStatus: { completed: 1, failed: 1 },
      byKind: { pdf: 2 },
      languages: { en: 1 },
    },
    files: [
      file(),
      file({ id: 'f2', filename: 'damaged-scan.jpg', kind: 'image', status: 'failed', error: 'OCR provider unavailable' }),
    ],
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, '', '/');
  listArchives.mockResolvedValue([]);
  getArchiveTimeline.mockResolvedValue({ events: [], total: 0, limitations: [] });
  listArchives.mockResolvedValue([]);
});

describe('Archive Studio', () => {
  it('shows the empty state when the account has no archives', async () => {
    render(<ArchivePage />);

    expect(await screen.findByText('No archives yet. Create the first one below.')).toBeInTheDocument();
    expect(screen.getByText('Select or create an archive')).toBeInTheDocument();
  });

  it('reports a list failure with a retry control', async () => {
    listArchives.mockRejectedValueOnce(new Error('boom')).mockResolvedValueOnce([]);

    render(<ArchivePage />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Could not load your archives');
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(listArchives).toHaveBeenCalledTimes(2));
  });

  it('opens an archive and shows per-file processing status', async () => {
    listArchives.mockResolvedValue([detail()]);
    getArchive.mockResolvedValue(detail());

    render(<ArchivePage />);

    const item = await screen.findByRole('button', { name: /Gujarat Stepwell Heritage Collection/ });
    expect(item).toHaveTextContent('2 file(s) · 4 entities');
    fireEvent.click(item);

    expect(await screen.findByText('rani-ki-vav-survey.pdf')).toBeInTheDocument();
    expect(screen.getByText('damaged-scan.jpg')).toBeInTheDocument();
    // A failed file must stay visible and carry its own reason.
    expect(screen.getByText(/OCR provider unavailable/)).toBeInTheDocument();
    expect(screen.getAllByText('Files').length).toBeGreaterThan(0);
  });

  it('does not fabricate data when the archive cannot be loaded', async () => {
    listArchives.mockResolvedValue([detail()]);
    getArchive.mockRejectedValue(new Error('nope'));

    render(<ArchivePage />);

    fireEvent.click(
      await screen.findByRole('button', { name: /Gujarat Stepwell Heritage Collection/ }),
    );

    expect(await screen.findByText('Could not load the archive.')).toBeInTheDocument();
    expect(screen.queryByText('rani-ki-vav-survey.pdf')).not.toBeInTheDocument();
  });
});
