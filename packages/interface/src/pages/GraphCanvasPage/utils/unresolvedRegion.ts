// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * The canvas's "Unresolved" region: pure grouping, layout and persistence.
 *
 * Every node that carries a pipeline flag (`utils/nodeFlags.ts`: kept
 * without a relationship, labelled by a reference, ...) is laid out together
 * inside one labelled corral beside the main graph, so the records the
 * pipeline kept but could not settle are visible in one place instead of
 * scattered through the layout.
 *
 * Presentation only. Nothing here adds or removes graph nodes or edges: the
 * collapsed "Unresolved (N)" summary is drawn on a canvas layer
 * (`hooks/useUnresolvedRegionLayer.ts`), collapsing hides the members through
 * the node reducer, and layout only moves member positions.
 */

import type Graph from 'graphology';
import { nodeFlags, type NodeFlag } from '../../../utils/nodeFlags';
import { readStoredString, writeStoredString } from '../../../utils/storage';
import type { NodeAttributes, EdgeAttributes } from '../types';
import { isSourceGroupNode } from '../types';

/** The region's name, drawn on its corral. */
export const UNRESOLVED_REGION_LABEL = 'Unresolved';

/** Per-viewer localStorage key for the region's expanded/collapsed state. */
export const UNRESOLVED_REGION_STORAGE_KEY = 'chaoscypher-graph-unresolved-region';

const COLLAPSED = 'collapsed';
const EXPANDED = 'expanded';

/** A node offered to the region: its id and its properties bag. */
export interface RegionCandidate {
  id: string;
  properties: unknown;
}

export interface UnresolvedRegion {
  /** Every flagged node, in input order. */
  memberIds: string[];
  /** The flags each member carries (drives the per-node badges). */
  flagsById: Map<string, NodeFlag[]>;
  /** Number of members (the N in "Unresolved (N)"). */
  count: number;
  /** Whether the region shows its members (true) or one summary node. */
  expanded: boolean;
  /** Members the collapse hides; empty while expanded. */
  hiddenIds: Set<string>;
  /** "Unresolved (N)" while collapsed with members; null otherwise. */
  summaryLabel: string | null;
}

/** The collapsed summary node's label. */
export function unresolvedSummaryLabel(count: number): string {
  return `${UNRESOLVED_REGION_LABEL} (${count})`;
}

/**
 * Which nodes belong in the region, and how it presents them.
 *
 * @param nodes - Candidate nodes with their properties.
 * @param expanded - The viewer's region state.
 */
export function groupUnresolvedNodes(
  nodes: Iterable<RegionCandidate>,
  expanded: boolean,
): UnresolvedRegion {
  const memberIds: string[] = [];
  const flagsById = new Map<string, NodeFlag[]>();
  for (const node of nodes) {
    const flags = nodeFlags(node.properties);
    if (flags.length === 0) continue;
    memberIds.push(node.id);
    flagsById.set(node.id, flags);
  }
  const count = memberIds.length;
  return {
    memberIds,
    flagsById,
    count,
    expanded,
    hiddenIds: expanded ? new Set() : new Set(memberIds),
    summaryLabel: !expanded && count > 0 ? unresolvedSummaryLabel(count) : null,
  };
}

/** The canvas graph's real nodes as region candidates (virtual nodes skipped). */
export function regionCandidatesFromGraph(
  graph: Graph<NodeAttributes, EdgeAttributes>,
): RegionCandidate[] {
  const candidates: RegionCandidate[] = [];
  graph.forEachNode((id, attrs) => {
    if (isSourceGroupNode(id)) return;
    candidates.push({ id, properties: attrs.content });
  });
  return candidates;
}

/** Ids of the canvas graph's flagged nodes (the region's members). */
export function unresolvedMemberIds(graph: Graph<NodeAttributes, EdgeAttributes>): string[] {
  return groupUnresolvedNodes(regionCandidatesFromGraph(graph), true).memberIds;
}

// ── Layout ──────────────────────────────────────────────────────────

export interface Point {
  x: number;
  y: number;
}

export interface Bounds {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

/**
 * Bounding box of points, each optionally with a radius, grown by `padding`.
 * Null for no points.
 */
export function boundsOf(
  points: Iterable<Point & { r?: number }>,
  padding = 0,
): Bounds | null {
  let bounds: Bounds | null = null;
  for (const p of points) {
    const r = (p.r ?? 0) + padding;
    if (!bounds) {
      bounds = { minX: p.x - r, minY: p.y - r, maxX: p.x + r, maxY: p.y + r };
      continue;
    }
    bounds.minX = Math.min(bounds.minX, p.x - r);
    bounds.minY = Math.min(bounds.minY, p.y - r);
    bounds.maxX = Math.max(bounds.maxX, p.x + r);
    bounds.maxY = Math.max(bounds.maxY, p.y + r);
  }
  return bounds;
}

/** Distance between region members, in graph units. */
const REGION_SPACING = 24;

/**
 * Place the region's members in a square grid to the right of the rest of
 * the graph, vertically centred on it, with a clear gap between the two.
 * With nothing else on the canvas the grid is centred on the origin.
 *
 * @param memberIds - The region's members, placed in this order.
 * @param others - Positions of every node outside the region.
 * @returns A position for every member.
 */
export function layoutUnresolvedRegion(
  memberIds: readonly string[],
  others: Iterable<Point>,
  spacing = REGION_SPACING,
): Map<string, Point> {
  const positions = new Map<string, Point>();
  if (memberIds.length === 0) return positions;

  const cols = Math.ceil(Math.sqrt(memberIds.length));
  const rows = Math.ceil(memberIds.length / cols);
  const width = (cols - 1) * spacing;
  const height = (rows - 1) * spacing;

  const main = boundsOf(others);
  let originX: number;
  let originY: number;
  if (main) {
    const gap = Math.max(spacing * 4, (main.maxX - main.minX) * 0.1);
    originX = main.maxX + gap;
    originY = (main.minY + main.maxY) / 2 - height / 2;
  } else {
    originX = -width / 2;
    originY = -height / 2;
  }

  memberIds.forEach((id, index) => {
    positions.set(id, {
      x: originX + (index % cols) * spacing,
      y: originY + Math.floor(index / cols) * spacing,
    });
  });
  return positions;
}

// ── Persistence ─────────────────────────────────────────────────────

/** The viewer's stored region state. Expanded unless they collapsed it. */
export function readUnresolvedRegionExpanded(): boolean {
  return readStoredString(UNRESOLVED_REGION_STORAGE_KEY) !== COLLAPSED;
}

/** Remember the viewer's region state; silently a no-op without storage. */
export function writeUnresolvedRegionExpanded(expanded: boolean): void {
  writeStoredString(UNRESOLVED_REGION_STORAGE_KEY, expanded ? EXPANDED : COLLAPSED);
}
