// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/** Dashboard statistics from /counts + /quality/summary endpoints. */
export interface DashboardStats {
  entityCount: number;
  relationCount: number;
  sourceCount: number;
  templateCount: number;
  lensCount: number;
  workflowCount: number;
  qualityScore: number | null;
  /** Avg relationships per entity. */
  avgRelations: number;
  /** Directed graph density as a percentage (0–100). */
  density: number;
  /** Avg relationships extracted per source document. */
  edgesPerSource: number;
}

/** A single activity log entry for the system health cluster. */
export interface ActivityEntry {
  id: string;
  time: string;
  message: string;
}

// GraphNode / GraphEdge moved to src/components/graphConstellation/graphLayout.ts
// (shared by the DashboardGraph background and the per-source Knowledge map).

