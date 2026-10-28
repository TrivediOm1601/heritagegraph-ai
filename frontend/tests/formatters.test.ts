import { describe, expect, it } from 'vitest';

import {
  formatBytes,
  formatConfidence,
  pluralizeCount,
  truncateText,
} from '../src/utils/formatters';

describe('formatBytes', () => {
  it('formats bytes, kilobytes and megabytes', () => {
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(2048)).toBe('2 KB');
    expect(formatBytes(10 * 1024 * 1024)).toBe('10.0 MB');
  });
});

describe('formatConfidence', () => {
  it('renders 0..1 scores as percentages', () => {
    expect(formatConfidence(0.85)).toBe('85%');
    expect(formatConfidence(0)).toBe('0%');
    expect(formatConfidence(1)).toBe('100%');
  });

  it('renders missing values as an em dash and clamps out-of-range ones', () => {
    expect(formatConfidence(undefined)).toBe('—');
    expect(formatConfidence(null)).toBe('—');
    expect(formatConfidence(Number.NaN)).toBe('—');
    expect(formatConfidence(1.5)).toBe('100%');
    expect(formatConfidence(-0.2)).toBe('0%');
  });
});

describe('truncateText', () => {
  it('keeps short text and ellipsizes long text', () => {
    expect(truncateText('short', 10)).toBe('short');
    expect(truncateText('a much longer inscription', 10)).toBe('a much lo…');
    expect(truncateText('anything', 0)).toBe('');
  });
});

describe('pluralizeCount', () => {
  it('picks singular and plural forms', () => {
    expect(pluralizeCount(1, 'entity', 'entities')).toBe('1 entity');
    expect(pluralizeCount(0, 'entity', 'entities')).toBe('0 entities');
    expect(pluralizeCount(3, 'record')).toBe('3 records');
  });
});
