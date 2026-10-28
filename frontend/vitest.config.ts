import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Vitest uses this config instead of vite.config.ts, so the app build
// config stays untouched and Tailwind never runs during tests.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}'],
    restoreMocks: true,
    unstubGlobals: true,
  },
});
