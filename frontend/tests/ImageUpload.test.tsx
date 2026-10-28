import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { ImageUpload } from '../src/components/upload/ImageUpload';
import { ANALYSIS_ENDPOINT, ANALYSIS_FILE_FIELD } from '../src/services/api';

/** Minimal Response stand-in: api.ts only reads ok/status/json/text. */
function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

function pngFile(name = 'manuscript.png'): File {
  return new File([new Uint8Array([137, 80, 78, 71])], name, { type: 'image/png' });
}

const fetchMock = vi.fn();

function getUploadInput(): HTMLInputElement {
  const input = document.querySelector('input[type="file"]');
  if (!input) throw new Error('file input not found');
  return input as HTMLInputElement;
}

function chooseFile(file: File): void {
  fireEvent.change(getUploadInput(), { target: { files: [file] } });
}

describe('ImageUpload', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockReset();
  });

  it('shows a local preview and submits a valid file as multipart form data', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ analysisId: 'an-42', status: 'queued' }, 201));
    const onSubmitted = vi.fn();

    render(<ImageUpload onSubmitted={onSubmitted} />);
    chooseFile(pngFile());

    // Local preview appears without any network call.
    expect(await screen.findByAltText('Preview of manuscript.png')).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Submit for analysis' }));

    expect(
      await screen.findByText('Upload accepted — analysis an-42 is queued.'),
    ).toBeInTheDocument();
    expect(onSubmitted).toHaveBeenCalledWith({ analysisId: 'an-42', status: 'queued' });

    // Agreed multipart request format.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(ANALYSIS_ENDPOINT);
    expect(init.method).toBe('POST');
    expect(init.body).toBeInstanceOf(FormData);
    const sent = (init.body as FormData).get(ANALYSIS_FILE_FIELD);
    expect(sent).toBeInstanceOf(File);
    expect((sent as File).name).toBe('manuscript.png');
  });

  it('rejects an unsupported file in the browser without contacting the backend', async () => {
    render(<ImageUpload />);

    chooseFile(new File(['plain text'], 'notes.txt', { type: 'text/plain' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      '"notes.txt" is not a supported image',
    );
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Submit for analysis' })).toBeDisabled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('shows an error and never success when the upload fails', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ error: 'OCR worker unavailable' }, 500));

    render(<ImageUpload />);
    chooseFile(pngFile());
    fireEvent.click(screen.getByRole('button', { name: 'Submit for analysis' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Upload failed.');
    expect(alert).toHaveTextContent('OCR worker unavailable');

    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.queryByText(/upload accepted/i)).not.toBeInTheDocument();
    // The form stays usable so the user can retry.
    expect(screen.getByRole('button', { name: 'Submit for analysis' })).toBeEnabled();
  });

  it('shows an error when a 2xx response carries an error payload', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ error: 'quota exceeded' }, 200));

    render(<ImageUpload />);
    chooseFile(pngFile());
    fireEvent.click(screen.getByRole('button', { name: 'Submit for analysis' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('quota exceeded');
    expect(screen.queryByText(/upload accepted/i)).not.toBeInTheDocument();
  });

  it('revokes preview object URLs when replaced, cleared and on unmount', () => {
    const createSpy = vi
      .spyOn(URL, 'createObjectURL')
      .mockReturnValueOnce('blob:preview-1')
      .mockReturnValueOnce('blob:preview-2');
    const revokeSpy = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});

    const { unmount } = render(<ImageUpload />);

    chooseFile(pngFile('one.png'));
    expect(createSpy).toHaveBeenCalledTimes(1);

    // Replacing the selection revokes the previous preview's URL.
    chooseFile(pngFile('two.png'));
    expect(revokeSpy).toHaveBeenCalledWith('blob:preview-1');
    expect(revokeSpy).not.toHaveBeenCalledWith('blob:preview-2');

    // Unmounting revokes the current preview's URL.
    unmount();
    expect(revokeSpy).toHaveBeenCalledWith('blob:preview-2');
  });

  it('disables the submit button and fires a single request while submitting', async () => {
    const deferred: { resolve: ((response: Response) => void) | null } = { resolve: null };
    fetchMock.mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          deferred.resolve = resolve;
        }),
    );

    render(<ImageUpload />);
    chooseFile(pngFile());

    const submitButton = screen.getByRole('button', { name: 'Submit for analysis' });
    fireEvent.click(submitButton);

    expect(await screen.findByRole('status')).toHaveTextContent('Uploading manuscript.png');
    expect(screen.getByRole('button', { name: 'Uploading…' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Uploading…' }));
    expect(fetchMock).toHaveBeenCalledTimes(1);

    deferred.resolve?.(jsonResponse({ analysisId: 'an-7', status: 'queued' }, 202));
    expect(await screen.findByText(/analysis an-7 is queued/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});