// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Queue monitor utilities — task description, details, and color mappers.
 */

/** Loose shape for task fields used by description/detail helpers. */
interface TaskLike {
  started_at?: string | number | null;
  completed_at?: string | number | null;
  operation: string;
  data?: {
    inputs?: { filename?: string; analysis_depth?: string };
    operations?: unknown[];
  };
  metadata?: {
    tool?: string;
    workflow_type?: string;
    thinking_enabled?: boolean;
  };
}

type MuiChipColor =
  | 'default'
  | 'primary'
  | 'secondary'
  | 'success'
  | 'error'
  | 'warning'
  | 'info';

/** Map task status to MUI Chip color. */
export function getStatusColor(status: string): MuiChipColor {
  switch (status) {
    case 'queued':
      return 'default';
    case 'running':
      return 'primary';
    case 'completed':
      return 'success';
    case 'failed':
      return 'error';
    case 'cancelled':
      return 'secondary';
    default:
      return 'default';
  }
}

/** Map numeric priority to MUI Chip color. */
export function getPriorityColor(priority: number): MuiChipColor {
  if (priority >= 80) return 'error';
  if (priority >= 60) return 'warning';
  if (priority >= 40) return 'primary';
  return 'default';
}

/** Derive a human-readable description from task metadata. */
export function getTaskDescription(task: TaskLike): string {
  if (task.operation === 'execute_workflow' && task.data?.inputs?.filename) {
    return task.data.inputs.filename;
  }
  if (task.metadata?.tool) {
    return task.metadata.tool;
  }
  if (task.operation === 'bulk_nodes' || task.operation === 'bulk_edges') {
    const count = task.data?.operations?.length || 0;
    return `${count} operations`;
  }
  if (task.operation === 'export_graph') {
    return 'Graph export';
  }
  if (task.operation === 'import_ccx') {
    return 'CCX import';
  }
  return task.operation.replace(/_/g, ' ');
}

/** Extract supplementary detail chips from task metadata. */
export function getTaskDetails(task: TaskLike): string[] {
  const details: string[] = [];
  if (task.operation === 'execute_workflow' && task.data?.inputs) {
    const inputs = task.data.inputs;
    if (inputs.analysis_depth) {
      details.push(`Analysis: ${inputs.analysis_depth}`);
    }
    if (task.metadata?.workflow_type) {
      details.push(task.metadata.workflow_type);
    }
  }
  if (task.metadata?.thinking_enabled) {
    details.push('Thinking enabled');
  }
  return details;
}

/** Status priority ordering for sort (lower = higher display priority). */
const STATUS_SORT_ORDER: Record<string, number> = {
  running: 0,
  queued: 1,
  failed: 2,
  completed: 3,
  cancelled: 4,
};

/** Sort tasks: running first, then queued, then by descending created_at. */
export function sortTasks<T extends { status: string; created_at?: number }>(
  tasks: T[],
): T[] {
  return [...tasks].sort((a, b) => {
    const aPriority = STATUS_SORT_ORDER[a.status] ?? 99;
    const bPriority = STATUS_SORT_ORDER[b.status] ?? 99;
    if (aPriority !== bPriority) return aPriority - bPriority;
    return (b.created_at || 0) - (a.created_at || 0);
  });
}
