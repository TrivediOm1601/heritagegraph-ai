/**
 * A5 — integration checks against the REAL backend.
 *
 * These tests run only when VITE_API_BASE_URL is set, e.g.:
 *
 *   VITE_API_BASE_URL=https://api.example.com npm run test:integration
 *
 * (Read via import.meta.env, which Vitest populates from the shell
 * environment and .env files alike.)
 *
 * Without it they are skipped, so `npm test` stays green while Module B is
 * unfinished. This is the gated placeholder for the plan's "one successful
 * real API interaction" evidence.
 */
import { describe, expect, it } from 'vitest';

import { searchHistoricalRecords } from '../src/services/api';

const baseUrl = (import.meta.env.VITE_API_BASE_URL ?? '').trim();

describe.skipIf(baseUrl === '')('real backend integration (VITE_API_BASE_URL)', () => {
  it('performs a real historical search and parses the contract envelope', async () => {
    const response = await searchHistoricalRecords('Ada Lovelace');

    expect(response.query).toBeTruthy();
    expect(Array.isArray(response.results)).toBe(true);
    for (const result of response.results) {
      expect(typeof result.id).toBe('string');
      expect(typeof result.title).toBe('string');
    }
  }, 20_000);

  it('reports API failures as errors rather than empty results', async () => {
    // A query below the guard never reaches the network.
    await expect(searchHistoricalRecords(' ')).rejects.toThrow(/at least 2 characters/);
  });
});