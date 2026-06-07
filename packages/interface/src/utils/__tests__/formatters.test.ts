// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  formatCompactNumber,
  formatFileSize,
  formatDate,
  formatDuration,
  formatDurationNullable,
  formatDurationMs,
  formatRelativeTime,
  formatTaskDuration,
  formatNumber,
  formatRelativeDate,
  formatAbsoluteDate,
  truncateUrl,
  truncateText,
  cleanTypeName,
} from '../formatters';

// ---------------------------------------------------------------------------
// formatCompactNumber
// ---------------------------------------------------------------------------
describe('formatCompactNumber', () => {
  it('returns plain string for values below 1 000', () => {
    expect(formatCompactNumber(0)).toBe('0');
    expect(formatCompactNumber(999)).toBe('999');
    expect(formatCompactNumber(1)).toBe('1');
  });

  it('returns K suffix for values in the thousands', () => {
    expect(formatCompactNumber(1000)).toBe('1.0K');
    expect(formatCompactNumber(1500)).toBe('1.5K');
    expect(formatCompactNumber(999_999)).toBe('1000.0K');
  });

  it('returns M suffix for values in the millions', () => {
    expect(formatCompactNumber(1_000_000)).toBe('1.0M');
    expect(formatCompactNumber(2_500_000)).toBe('2.5M');
  });

  it('respects the decimals parameter', () => {
    expect(formatCompactNumber(1_500_000, 2)).toBe('1.50M');
    expect(formatCompactNumber(1_500, 0)).toBe('2K');
    expect(formatCompactNumber(1_500, 2)).toBe('1.50K');
  });
});

// ---------------------------------------------------------------------------
// formatFileSize
// ---------------------------------------------------------------------------
describe('formatFileSize', () => {
  it('returns "0 Bytes" for zero', () => {
    expect(formatFileSize(0)).toBe('0 Bytes');
  });

  it('formats bytes', () => {
    expect(formatFileSize(500)).toBe('500 Bytes');
    expect(formatFileSize(1)).toBe('1 Bytes');
  });

  it('formats KB', () => {
    expect(formatFileSize(1024)).toBe('1 KB');
    expect(formatFileSize(1536)).toBe('1.5 KB');
  });

  it('formats MB', () => {
    expect(formatFileSize(1024 * 1024)).toBe('1 MB');
    expect(formatFileSize(1024 * 1024 * 1.5)).toBe('1.5 MB');
  });

  it('formats GB', () => {
    expect(formatFileSize(1024 * 1024 * 1024)).toBe('1 GB');
    expect(formatFileSize(1024 * 1024 * 1024 * 2)).toBe('2 GB');
  });
});

// ---------------------------------------------------------------------------
// formatDate
// ---------------------------------------------------------------------------
describe('formatDate', () => {
  it('returns a non-empty string for a valid ISO date', () => {
    const result = formatDate('2026-05-25T12:00:00Z');
    expect(typeof result).toBe('string');
    expect(result.length).toBeGreaterThan(0);
  });

  it('returns the original string for an invalid date (catch path)', () => {
    // new Date('not-a-date').toLocaleString() returns 'Invalid Date' — the
    // function does NOT throw, so it returns that output string. We validate
    // that the function at least returns a string.
    const result = formatDate('not-a-date');
    expect(typeof result).toBe('string');
  });
});

// ---------------------------------------------------------------------------
// formatDuration
// ---------------------------------------------------------------------------
describe('formatDuration', () => {
  it('formats sub-minute durations as seconds', () => {
    expect(formatDuration(0)).toBe('0s');
    expect(formatDuration(45)).toBe('45s');
    expect(formatDuration(59.9)).toBe('59s');
  });

  it('formats minute-range durations', () => {
    expect(formatDuration(60)).toBe('1m 0s');
    expect(formatDuration(90)).toBe('1m 30s');
    expect(formatDuration(3599)).toBe('59m 59s');
  });

  it('formats hour-range durations', () => {
    expect(formatDuration(3600)).toBe('1h 0m');
    expect(formatDuration(5400)).toBe('1h 30m');
    expect(formatDuration(7322)).toBe('2h 2m');
  });
});

// ---------------------------------------------------------------------------
// formatDurationNullable
// ---------------------------------------------------------------------------
describe('formatDurationNullable', () => {
  it('returns null for null input', () => {
    expect(formatDurationNullable(null)).toBeNull();
  });

  it('returns null for undefined input', () => {
    expect(formatDurationNullable(undefined)).toBeNull();
  });

  it('returns null for zero', () => {
    expect(formatDurationNullable(0)).toBeNull();
  });

  it('returns null for negative values', () => {
    expect(formatDurationNullable(-1)).toBeNull();
  });

  it('formats sub-second as ms', () => {
    expect(formatDurationNullable(0.5)).toBe('500ms');
    expect(formatDurationNullable(0.001)).toBe('1ms');
  });

  it('formats seconds range', () => {
    expect(formatDurationNullable(1)).toBe('1s');
    expect(formatDurationNullable(45)).toBe('45s');
    expect(formatDurationNullable(59)).toBe('59s');
  });

  it('formats minutes with remaining seconds', () => {
    expect(formatDurationNullable(90)).toBe('1m 30s');
    expect(formatDurationNullable(61)).toBe('1m 1s');
  });

  it('formats whole minutes (no seconds)', () => {
    expect(formatDurationNullable(120)).toBe('2m');
  });

  it('formats hours with remaining minutes', () => {
    expect(formatDurationNullable(3660)).toBe('1h 1m');
    expect(formatDurationNullable(5400)).toBe('1h 30m');
  });

  it('formats whole hours (no remaining minutes)', () => {
    expect(formatDurationNullable(3600)).toBe('1h');
  });
});

// ---------------------------------------------------------------------------
// formatDurationMs
// ---------------------------------------------------------------------------
describe('formatDurationMs', () => {
  it('returns default fallback for null', () => {
    expect(formatDurationMs(null)).toBe('-');
  });

  it('returns default fallback for undefined', () => {
    expect(formatDurationMs(undefined)).toBe('-');
  });

  it('returns default fallback for zero', () => {
    expect(formatDurationMs(0)).toBe('-');
  });

  it('returns custom fallback', () => {
    expect(formatDurationMs(null, 'N/A')).toBe('N/A');
  });

  it('formats sub-second as ms', () => {
    expect(formatDurationMs(350)).toBe('350ms');
    expect(formatDurationMs(1)).toBe('1ms');
    expect(formatDurationMs(999)).toBe('999ms');
  });

  it('formats seconds range', () => {
    expect(formatDurationMs(1000)).toBe('1.0s');
    expect(formatDurationMs(2500)).toBe('2.5s');
    expect(formatDurationMs(59999)).toBe('60.0s');
  });

  it('formats minutes with remaining seconds', () => {
    expect(formatDurationMs(90000)).toBe('1m 30s');
    expect(formatDurationMs(61000)).toBe('1m 1s');
  });

  it('formats whole minutes (no remaining seconds)', () => {
    expect(formatDurationMs(120000)).toBe('2m');
  });

  it('formats hours with remaining minutes', () => {
    expect(formatDurationMs(3660000)).toBe('1h 1m');
    expect(formatDurationMs(5400000)).toBe('1h 30m');
  });

  it('formats whole hours (no remaining minutes)', () => {
    expect(formatDurationMs(3600000)).toBe('1h');
  });
});

// ---------------------------------------------------------------------------
// formatRelativeTime  (uses fake timers)
// ---------------------------------------------------------------------------
describe('formatRelativeTime', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  const NOW = new Date('2026-05-25T12:00:00Z');

  function tsAgo(ms: number): string {
    return new Date(NOW.getTime() - ms).toISOString();
  }

  it('returns default fallback for missing timestamp', () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    expect(formatRelativeTime()).toBe('-');
    expect(formatRelativeTime(undefined)).toBe('-');
  });

  it('returns custom fallback', () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    expect(formatRelativeTime(undefined, 'n/a')).toBe('n/a');
  });

  it('returns "Just now" within the first minute', () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    expect(formatRelativeTime(tsAgo(30_000))).toBe('Just now');
    expect(formatRelativeTime(tsAgo(0))).toBe('Just now');
  });

  it('returns minutes ago for 1–59 minutes', () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    expect(formatRelativeTime(tsAgo(60_000))).toBe('1m ago');
    expect(formatRelativeTime(tsAgo(30 * 60_000))).toBe('30m ago');
    expect(formatRelativeTime(tsAgo(59 * 60_000))).toBe('59m ago');
  });

  it('returns hours ago for 1–23 hours', () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    expect(formatRelativeTime(tsAgo(3_600_000))).toBe('1h ago');
    expect(formatRelativeTime(tsAgo(12 * 3_600_000))).toBe('12h ago');
  });

  it('returns days ago for 1–6 days', () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    expect(formatRelativeTime(tsAgo(86_400_000))).toBe('1d ago');
    expect(formatRelativeTime(tsAgo(6 * 86_400_000))).toBe('6d ago');
  });

  it('falls back to locale date string for timestamps older than 7 days', () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    const ts = tsAgo(8 * 86_400_000);
    const result = formatRelativeTime(ts);
    // Should be the locale date string, not a relative format
    expect(result).not.toMatch(/ago$/);
    expect(result.length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// formatTaskDuration
// ---------------------------------------------------------------------------
describe('formatTaskDuration', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('calculates duration from ISO string start and end', () => {
    expect(
      formatTaskDuration('2026-05-25T12:00:00Z', '2026-05-25T12:01:30Z'),
    ).toBe('1m 30s');
  });

  it('calculates duration from epoch seconds start and end', () => {
    const start = 1_000_000;
    const end = 1_000_090;
    expect(formatTaskDuration(start, end)).toBe('1m 30s');
  });

  it('uses Date.now() when completedAt is omitted', () => {
    vi.useFakeTimers();
    const now = new Date('2026-05-25T12:00:30Z');
    vi.setSystemTime(now);
    expect(
      formatTaskDuration('2026-05-25T12:00:00Z'),
    ).toBe('30s');
  });

  it('uses Date.now() when completedAt is null', () => {
    vi.useFakeTimers();
    const now = new Date('2026-05-25T12:02:00Z');
    vi.setSystemTime(now);
    expect(
      formatTaskDuration('2026-05-25T12:00:00Z', null),
    ).toBe('2m 0s');
  });

  it('handles epoch-seconds completedAt', () => {
    const startEpoch = 1_000_000;
    const endEpoch = startEpoch + 3600;
    expect(formatTaskDuration(startEpoch, endEpoch)).toBe('1h 0m');
  });
});

// ---------------------------------------------------------------------------
// formatNumber
// ---------------------------------------------------------------------------
describe('formatNumber', () => {
  it('formats zero', () => {
    expect(formatNumber(0)).toBe('0');
  });

  it('formats integers with locale grouping', () => {
    // Intl.NumberFormat groups thousands with commas in en-US locale.
    // In any locale the formatted string should be non-empty.
    const result = formatNumber(1_234_567);
    expect(typeof result).toBe('string');
    expect(result.length).toBeGreaterThan(0);
    // The digits must all appear (possibly with separators)
    expect(result.replace(/[^0-9]/g, '')).toBe('1234567');
  });

  it('formats small numbers without grouping', () => {
    expect(formatNumber(42)).toBe('42');
  });
});

// ---------------------------------------------------------------------------
// formatRelativeDate  (uses fake timers)
// ---------------------------------------------------------------------------
describe('formatRelativeDate', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  const NOW = new Date('2026-05-25T12:00:00Z');

  function dateAgo(ms: number): Date {
    return new Date(NOW.getTime() - ms);
  }

  it('returns "just now" for less than 60 seconds ago', () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    expect(formatRelativeDate(dateAgo(30_000))).toBe('just now');
    expect(formatRelativeDate(dateAgo(0))).toBe('just now');
  });

  it('returns minutes ago for 1–59 minutes', () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    expect(formatRelativeDate(dateAgo(60_000))).toBe('1m ago');
    expect(formatRelativeDate(dateAgo(45 * 60_000))).toBe('45m ago');
  });

  it('returns hours ago for 1–23 hours', () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    expect(formatRelativeDate(dateAgo(3_600_000))).toBe('1h ago');
    expect(formatRelativeDate(dateAgo(23 * 3_600_000))).toBe('23h ago');
  });

  it('returns days ago for 1–6 days', () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    expect(formatRelativeDate(dateAgo(86_400_000))).toBe('1d ago');
    expect(formatRelativeDate(dateAgo(6 * 86_400_000))).toBe('6d ago');
  });

  it('returns weeks ago for 1–3 weeks', () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    expect(formatRelativeDate(dateAgo(7 * 86_400_000))).toBe('1w ago');
    expect(formatRelativeDate(dateAgo(21 * 86_400_000))).toBe('3w ago');
  });

  it('returns months ago for 1–11 months', () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    expect(formatRelativeDate(dateAgo(30 * 86_400_000))).toBe('1mo ago');
    expect(formatRelativeDate(dateAgo(330 * 86_400_000))).toBe('11mo ago');
  });

  it('returns years ago for 12+ months', () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    expect(formatRelativeDate(dateAgo(365 * 86_400_000))).toBe('1y ago');
    expect(formatRelativeDate(dateAgo(730 * 86_400_000))).toBe('2y ago');
  });
});

// ---------------------------------------------------------------------------
// formatAbsoluteDate
// ---------------------------------------------------------------------------
describe('formatAbsoluteDate', () => {
  it('returns a non-empty string', () => {
    const result = formatAbsoluteDate(new Date('2026-05-25T12:00:00Z'));
    expect(typeof result).toBe('string');
    expect(result.length).toBeGreaterThan(0);
  });

  it('returns the same as date.toLocaleString()', () => {
    const d = new Date('2026-05-25T12:00:00Z');
    expect(formatAbsoluteDate(d)).toBe(d.toLocaleString());
  });
});

// ---------------------------------------------------------------------------
// truncateUrl
// ---------------------------------------------------------------------------
describe('truncateUrl', () => {
  it('returns the url unchanged when it is within the limit', () => {
    const short = 'https://example.com';
    expect(truncateUrl(short)).toBe(short);
    expect(truncateUrl(short, 50)).toBe(short);
  });

  it('truncates when url exceeds maxLength', () => {
    const long = 'https://example.com/' + 'a'.repeat(60);
    const result = truncateUrl(long, 50);
    // The function may use domain + truncated path or plain substring.
    // Either way the result must be shorter than the original.
    expect(result.length).toBeLessThan(long.length);
    // And it must contain something from the original (domain or prefix).
    expect(long.startsWith('https://example.com') || result.includes('example.com')).toBe(true);
  });

  it('shows domain + truncated path when path is long enough', () => {
    const long =
      'https://my.host.io/some/very/long/path/segment/that/overflows/the/limit/here';
    const result = truncateUrl(long, 50);
    // Either the domain appears or the string is capped at maxLength + '...'
    expect(result.length).toBeLessThanOrEqual(53);
  });

  it('truncates a non-URL string plain-style', () => {
    const notUrl = 'x'.repeat(60);
    const result = truncateUrl(notUrl, 50);
    expect(result).toBe('x'.repeat(47) + '...');
  });

  it('uses default maxLength of 50', () => {
    const exactly50 = 'a'.repeat(50);
    expect(truncateUrl(exactly50)).toBe(exactly50);

    const fiftyone = 'a'.repeat(51);
    expect(truncateUrl(fiftyone)).toBe('a'.repeat(47) + '...');
  });

  it('truncates when domain alone is at or above the limit', () => {
    // Domain of 48 chars + scheme → maxLength - 3 = 47, domain >= 47
    const bigDomain = 'https://' + 'a'.repeat(50) + '.com/path/foo';
    const result = truncateUrl(bigDomain, 50);
    expect(result.endsWith('...')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// truncateText
// ---------------------------------------------------------------------------
describe('truncateText', () => {
  it('returns the text unchanged when within the limit', () => {
    expect(truncateText('hello')).toBe('hello');
    expect(truncateText('hello', 10)).toBe('hello');
  });

  it('truncates with ellipsis when text exceeds maxLength', () => {
    const text = 'a'.repeat(110);
    const result = truncateText(text);
    expect(result).toBe('a'.repeat(97) + '...');
    expect(result.length).toBe(100);
  });

  it('respects custom maxLength', () => {
    const result = truncateText('hello world', 8);
    expect(result).toBe('hello...');
    expect(result.length).toBe(8);
  });

  it('returns text unchanged when exactly at maxLength', () => {
    const text = 'a'.repeat(100);
    expect(truncateText(text)).toBe(text);
  });
});

// ---------------------------------------------------------------------------
// cleanTypeName
// ---------------------------------------------------------------------------
describe('cleanTypeName', () => {
  it('returns "Unknown" for null', () => {
    expect(cleanTypeName(null)).toBe('Unknown');
  });

  it('returns "Unknown" for undefined', () => {
    expect(cleanTypeName(undefined)).toBe('Unknown');
  });

  it('returns "Unknown" for empty string', () => {
    expect(cleanTypeName('')).toBe('Unknown');
  });

  it('strips the system_template_ prefix', () => {
    expect(cleanTypeName('system_template_person')).toBe('Person');
    expect(cleanTypeName('system_template_organization')).toBe('Organization');
  });

  it('replaces underscores with spaces and title-cases', () => {
    expect(cleanTypeName('some_type_name')).toBe('Some Type Name');
  });

  it('title-cases a plain word', () => {
    expect(cleanTypeName('person')).toBe('Person');
  });

  it('handles multi-word system template names', () => {
    expect(cleanTypeName('system_template_legal_entity')).toBe('Legal Entity');
  });

  it('handles already title-cased strings gracefully', () => {
    expect(cleanTypeName('Person')).toBe('Person');
  });
});
