// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for QueueMonitorPage pure utilities.
 *
 * `sortTasks` is the regression anchor: the backend sends ISO-8601
 * string timestamps, and the old numeric subtraction comparator
 * produced NaN on them — silently disabling the created_at tie-break.
 */
import { describe, it, expect } from 'vitest';
import { sortTasks, getStatusColor, getTaskDescription } from '../utils';

describe('sortTasks', () => {
  it('orders running before queued before finished statuses', () => {
    const tasks = [
      { status: 'completed', created_at: '2026-07-30T10:00:00+00:00' },
      { status: 'queued', created_at: '2026-07-30T10:00:00+00:00' },
      { status: 'running', created_at: '2026-07-30T10:00:00+00:00' },
      { status: 'failed', created_at: '2026-07-30T10:00:00+00:00' },
    ];
    expect(sortTasks(tasks).map((t) => t.status)).toEqual([
      'running',
      'queued',
      'failed',
      'completed',
    ]);
  });

  it('tie-breaks equal statuses by descending ISO created_at (newest first)', () => {
    const tasks = [
      { status: 'queued', created_at: '2026-07-30T09:00:00+00:00' },
      { status: 'queued', created_at: '2026-07-30T11:00:00+00:00' },
      { status: 'queued', created_at: '2026-07-30T10:00:00+00:00' },
    ];
    expect(sortTasks(tasks).map((t) => t.created_at)).toEqual([
      '2026-07-30T11:00:00+00:00',
      '2026-07-30T10:00:00+00:00',
      '2026-07-30T09:00:00+00:00',
    ]);
  });

  it('tolerates missing created_at (sorts after populated timestamps)', () => {
    const tasks = [
      { status: 'queued', created_at: undefined },
      { status: 'queued', created_at: '2026-07-30T10:00:00+00:00' },
    ];
    expect(sortTasks(tasks)[0].created_at).toBe('2026-07-30T10:00:00+00:00');
  });

  it('does not mutate the input array', () => {
    const tasks = [
      { status: 'completed', created_at: '2026-07-30T10:00:00+00:00' },
      { status: 'running', created_at: '2026-07-30T10:00:00+00:00' },
    ];
    const copy = [...tasks];
    sortTasks(tasks);
    expect(tasks).toEqual(copy);
  });
});

describe('getStatusColor', () => {
  it('maps every known status and defaults unknowns', () => {
    expect(getStatusColor('running')).toBe('primary');
    expect(getStatusColor('failed')).toBe('error');
    expect(getStatusColor('retried')).toBe('info');
    expect(getStatusColor('mystery')).toBe('default');
  });
});

describe('getTaskDescription', () => {
  it('humanizes plain operation names', () => {
    expect(getTaskDescription({ operation: 'rebuild_search_indexes' })).toBe(
      'rebuild search indexes',
    );
  });

  it('prefers workflow input filename when present', () => {
    expect(
      getTaskDescription({
        operation: 'execute_workflow',
        data: { inputs: { filename: 'notes.md' } },
      }),
    ).toBe('notes.md');
  });
});
