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
import {
  sortTasks,
  getStatusColor,
  getTaskDescription,
  getTaskDetails,
  getPriorityColor,
  hasActiveTasks,
} from '../utils';

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

describe('hasActiveTasks', () => {
  it('treats an unknown count (null/undefined) as "may have work" so Cancel All stays offered', () => {
    // The backend sends total_in_queue: null when queue stats are unavailable;
    // collapsing it to 0 once disabled Cancel All over a full backlog.
    expect(hasActiveTasks(null)).toBe(true);
    expect(hasActiveTasks(undefined)).toBe(true);
  });

  it('disables only on a known zero', () => {
    expect(hasActiveTasks(0)).toBe(false);
    expect(hasActiveTasks(1)).toBe(true);
    expect(hasActiveTasks(2000)).toBe(true);
  });
});

describe('getPriorityColor', () => {
  it('maps the 80/60/40 threshold ladder', () => {
    expect(getPriorityColor(100)).toBe('error');
    expect(getPriorityColor(80)).toBe('error');
    expect(getPriorityColor(79)).toBe('warning');
    expect(getPriorityColor(60)).toBe('warning');
    expect(getPriorityColor(59)).toBe('primary');
    expect(getPriorityColor(40)).toBe('primary');
    expect(getPriorityColor(39)).toBe('default');
    expect(getPriorityColor(0)).toBe('default');
  });
});

describe('getTaskDescription (bulk operations)', () => {
  it('reads the list-view operations_count whitelisted by _slim_task_data', () => {
    expect(
      getTaskDescription({ operation: 'bulk_nodes', data: { operations_count: 12 } }),
    ).toBe('12 operations');
  });

  it('falls back to the detail-view operations array length', () => {
    expect(
      getTaskDescription({ operation: 'bulk_edges', data: { operations: [{}, {}, {}] } }),
    ).toBe('3 operations');
  });

  it('reports 0 operations when neither shape is present', () => {
    expect(getTaskDescription({ operation: 'bulk_nodes' })).toBe('0 operations');
  });
});

describe('getTaskDetails', () => {
  it('collects analysis depth, workflow type and thinking chips', () => {
    expect(
      getTaskDetails({
        operation: 'execute_workflow',
        data: { inputs: { analysis_depth: 'deep' } },
        metadata: { workflow_type: 'research', thinking_enabled: true },
      }),
    ).toEqual(['Analysis: deep', 'research', 'Thinking enabled']);
  });

  it('returns no chips for a plain operation without metadata', () => {
    expect(getTaskDetails({ operation: 'export_graph' })).toEqual([]);
  });
});

describe('sortTasks (retried)', () => {
  it('sorts retried rows after cancelled instead of the unknown-status bucket', () => {
    const tasks = [
      { status: 'retried', created_at: '2026-09-17T10:00:00+00:00' },
      { status: 'cancelled', created_at: '2026-09-17T10:00:00+00:00' },
      { status: 'mystery', created_at: '2026-09-17T10:00:00+00:00' },
    ];
    expect(sortTasks(tasks).map((t) => t.status)).toEqual(['cancelled', 'retried', 'mystery']);
  });
});
