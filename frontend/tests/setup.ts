import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => {
  cleanup();
});

// jsdom does not implement object URLs, which the upload preview relies on.
let objectUrlCounter = 0;
URL.createObjectURL = () => `blob:mock-${(objectUrlCounter += 1)}`;
URL.revokeObjectURL = () => {};
