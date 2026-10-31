import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Photo3DViewer } from '../src/components/viewer/Photo3DViewer';

describe('Photo3DViewer', () => {
  it('offers a View in 3D button and opens a labelled viewer when clicked', () => {
    render(<Photo3DViewer src="blob:test-image" />);
    fireEvent.click(screen.getByRole('button', { name: 'View in 3D' }));
    expect(screen.getByRole('region', { name: '3D depth view' })).toBeInTheDocument();
    expect(screen.getByText(/estimated by AI from a single photo/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.getByRole('button', { name: 'View in 3D' })).toBeInTheDocument();
  });
});
