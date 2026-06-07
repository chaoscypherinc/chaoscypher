// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Shared graph-constellation building blocks: the d3-force layout pipeline and
 * the Canvas 2D rendering primitives, reused by the DashboardGraph background
 * and the per-source Knowledge map.
 */

export {
  computeConstellationLayout,
  type GraphNode,
  type GraphEdge,
  type RawNode,
  type RawEdge,
} from './graphLayout';
