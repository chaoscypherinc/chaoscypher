// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * The canvas's "Unresolved (N)" buckets: one synthetic hub per source document.
 *
 * Every node that carries a pipeline flag (`utils/nodeFlags.ts`: kept
 * without a relationship, labelled by a reference, ...) is linked to the
 * muted hub of the source document it came from (`bucketSourceOf`), and that
 * hub to the document's root node. Hubs are built the same way as a
 * source's root (`hubNodeAttributes`, `addHubEdge`) and laid out beside it,
 * their members around them. A node with no known source goes to one hub
 * with no document link. Hubs are canvas-only: `isSyntheticNode` keeps them
 * out of every data path, and a collapsed hub hides its members through the
 * node reducer.
 */

import type Graph from 'graphology';
import { nodeFlags } from '../../../utils/nodeFlags';
import { getIconDataUri } from '../../../utils/iconSprites';
import { ChaosCypherNeutrals } from '../../../theme/palette';
import type { NodeAttributes, EdgeAttributes } from '../types';
import {
  SOURCE_GROUP_PREFIX,
  UNRESOLVED_EDGE_PREFIX,
  UNRESOLVED_ROOT_EDGE_PREFIX,
  bucketSourceId,
  isSourceGroupNode,
  isSyntheticNode,
  isUnresolvedBucketNode,
  unresolvedBucketId,
} from '../types';
import { UNRESOLVED_HUB_SIZE, addHubEdge, centroidOf, hubNodeAttributes } from './transformers';

/** The hub's name. */
const UNRESOLVED_BUCKET_TITLE = 'Unresolved';

/** The hub's hover line: how to open or close it. */
export const UNRESOLVED_BUCKET_HINT = 'Double-click to show or hide its nodes';

/** A hub's label: "Unresolved (N)". */
export function unresolvedBucketLabel(count: number): string {
  return `${UNRESOLVED_BUCKET_TITLE} (${count})`;
}

/** Id of the synthetic edge linking a member to its hub. */
export function unresolvedEdgeId(nodeId: string): string {
  return `${UNRESOLVED_EDGE_PREFIX}${nodeId}`;
}

/** Id of the synthetic edge linking a document root to its Unresolved hub. */
export function unresolvedRootEdgeId(sourceId: string): string {
  return `${UNRESOLVED_ROOT_EDGE_PREFIX}${sourceId}`;
}

/** Ids of the graph's real nodes carrying a pipeline flag, in graph order. */
export function unresolvedMemberIds(graph: Graph<NodeAttributes, EdgeAttributes>): string[] {
  const ids: string[] = [];
  graph.forEachNode((id, attrs) => {
    if (isSyntheticNode(id)) return;
    if (nodeFlags(attrs.content).length > 0) ids.push(id);
  });
  return ids;
}

/**
 * The source document whose hub a node goes in: one only, by a stable rule.
 *
 * 1. Its home source (`sourceDocumentId`, the `source_id` the canvas gets
 *    for every node: the source it was committed from), even when other
 *    documents cite it too.
 * 2. Without one, the first document root it hangs off (its first
 *    provenance edge, in the order the roots loaded).
 * 3. With neither, `null`: the no-source hub.
 */
export function bucketSourceOf(
  graph: Graph<NodeAttributes, EdgeAttributes>,
  nodeId: string,
): string | null {
  const home = graph.getNodeAttribute(nodeId, 'sourceDocumentId');
  if (home) return home;
  for (const root of graph.inNeighbors(nodeId)) {
    if (isSourceGroupNode(root)) return root.slice(SOURCE_GROUP_PREFIX.length);
  }
  return null;
}

/** The unresolved nodes by hub id (`unresolvedBucketId`), hubs in graph order of first member. */
export function groupUnresolvedBySource(
  graph: Graph<NodeAttributes, EdgeAttributes>,
): Map<string, string[]> {
  const hubs = new Map<string, string[]>();
  for (const id of unresolvedMemberIds(graph)) {
    const hubId = unresolvedBucketId(bucketSourceOf(graph, id));
    const members = hubs.get(hubId);
    if (members) members.push(id);
    else hubs.set(hubId, [id]);
  }
  return hubs;
}

/** Add or recount one hub, link its members (moving any from another hub) and its root. */
function syncHub(
  graph: Graph<NodeAttributes, EdgeAttributes>,
  hubId: string,
  memberIds: string[],
): void {
  const label = unresolvedBucketLabel(memberIds.length);
  if (!graph.hasNode(hubId)) {
    const center = centroidOf(graph, memberIds) ?? { x: 0, y: 0 };
    graph.addNode(
      hubId,
      hubNodeAttributes({
        id: hubId,
        title: UNRESOLVED_BUCKET_TITLE,
        label,
        templateId: '__unresolved__',
        color: ChaosCypherNeutrals.textTertiary,
        image: getIconDataUri('QuestionMark'),
        size: UNRESOLVED_HUB_SIZE,
        x: center.x,
        y: center.y,
      }),
    );
  } else if (graph.getNodeAttribute(hubId, 'label') !== label) {
    graph.setNodeAttribute(hubId, 'label', label);
  }

  const members = new Set(memberIds);
  for (const edgeId of graph.outEdges(hubId)) {
    if (edgeId.startsWith(UNRESOLVED_EDGE_PREFIX) && !members.has(graph.target(edgeId))) {
      graph.dropEdge(edgeId);
    }
  }
  for (const id of memberIds) {
    const edgeId = unresolvedEdgeId(id);
    // A member that moved hubs drops its old link first.
    if (graph.hasEdge(edgeId) && graph.source(edgeId) !== hubId) graph.dropEdge(edgeId);
    addHubEdge(graph, edgeId, hubId, id, { templateId: '__unresolved__' });
  }

  const sourceId = bucketSourceId(hubId);
  if (sourceId) {
    addHubEdge(graph, unresolvedRootEdgeId(sourceId), `${SOURCE_GROUP_PREFIX}${sourceId}`, hubId, {
      templateId: '__unresolved__',
    });
  }
}

/**
 * Make the hubs and their edges match the graph's unresolved nodes.
 *
 * One hub per source with unresolved nodes (and one for those with no
 * known source), each added at its members' centroid, recounted, linked to
 * its members and to its document root when the root is on the canvas;
 * a hub with no member left is dropped with its edges. Idempotent: with
 * nothing to change it touches nothing.
 *
 * @returns The members by hub id.
 */
export function syncUnresolvedBuckets(
  graph: Graph<NodeAttributes, EdgeAttributes>,
): Map<string, string[]> {
  const hubs = groupUnresolvedBySource(graph);
  const stale: string[] = [];
  graph.forEachNode((id) => {
    if (isUnresolvedBucketNode(id) && !hubs.has(id)) stale.push(id);
  });
  for (const id of stale) graph.dropNode(id);
  for (const [hubId, memberIds] of hubs) syncHub(graph, hubId, memberIds);
  return hubs;
}

/**
 * What the hubs hide: the members of every closed hub, and every hub (with
 * its members) whose document is collapsed.
 *
 * @param hubs - Members by hub id.
 * @param expandedHubs - The hubs showing their members.
 * @param collapsedSources - Source ids whose document root is collapsed.
 */
export function unresolvedHiddenIds(
  hubs: ReadonlyMap<string, readonly string[]>,
  expandedHubs: ReadonlySet<string>,
  collapsedSources: ReadonlySet<string> = new Set(),
): Set<string> {
  const hidden = new Set<string>();
  for (const [hubId, memberIds] of hubs) {
    const sourceId = bucketSourceId(hubId);
    const documentCollapsed = sourceId !== null && collapsedSources.has(sourceId);
    if (documentCollapsed) hidden.add(hubId);
    if (documentCollapsed || !expandedHubs.has(hubId)) {
      for (const id of memberIds) hidden.add(id);
    }
  }
  return hidden;
}

// ── Layout ──────────────────────────────────────────────────────────

interface Point {
  x: number;
  y: number;
}

/** Distance between neighbouring members around a hub, in graph units. */
const MEMBER_SPACING = 24;
/** Sunflower angle: successive members never line up. */
const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

/**
 * Lay out each hub and its members, the way a source's nodes sit around
 * their root: the members in a sunflower spiral, one spacing clear of the
 * hub.
 *
 * - A hub with an anchor (its document root, or its document's nodes) sits
 *   just beside it, among that document's nodes.
 * - A hub without one (the no-source hub) is parked to the right of the
 *   rest of the graph, vertically centred on it (several in a column; on
 *   the origin when nothing else is on the canvas).
 *
 * @param hubs - Members by hub id, placed in this order.
 * @param anchors - Where each anchored hub's document sits.
 * @param others - Positions of every node outside the buckets.
 * @returns A position for every member and every hub.
 */
export function layoutUnresolvedBuckets(
  hubs: ReadonlyMap<string, readonly string[]>,
  anchors: ReadonlyMap<string, Point>,
  others: Iterable<Point>,
  spacing = MEMBER_SPACING,
): Map<string, Point> {
  const positions = new Map<string, Point>();
  const radiusOf = (index: number) => spacing * (1 + 0.6 * Math.sqrt(index));
  const outerRadiusOf = (memberIds: readonly string[]) => radiusOf(Math.max(memberIds.length - 1, 0));

  const place = (hubId: string, memberIds: readonly string[], center: Point) => {
    positions.set(hubId, center);
    memberIds.forEach((id, index) => {
      const r = radiusOf(index);
      const angle = index * GOLDEN_ANGLE;
      positions.set(id, { x: center.x + r * Math.cos(angle), y: center.y + r * Math.sin(angle) });
    });
  };

  const unanchored: Array<[string, readonly string[]]> = [];
  for (const [hubId, memberIds] of hubs) {
    if (memberIds.length === 0) continue;
    const anchor = anchors.get(hubId);
    if (!anchor) {
      unanchored.push([hubId, memberIds]);
      continue;
    }
    place(hubId, memberIds, { x: anchor.x + spacing * 2 + outerRadiusOf(memberIds), y: anchor.y });
  }
  if (unanchored.length === 0) return positions;

  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const p of others) {
    minX = Math.min(minX, p.x);
    maxX = Math.max(maxX, p.x);
    minY = Math.min(minY, p.y);
    maxY = Math.max(maxY, p.y);
  }
  const radii = unanchored.map(([, memberIds]) => outerRadiusOf(memberIds));
  const widest = Math.max(...radii);
  const column = radii.reduce((sum, r) => sum + 2 * r, 0) + spacing * 2 * (radii.length - 1);
  let x = 0;
  let midY = 0;
  if (maxX >= minX) {
    const gap = Math.max(spacing * 4, (maxX - minX) * 0.1);
    x = maxX + gap + widest;
    midY = (minY + maxY) / 2;
  }
  let y = midY - column / 2;
  unanchored.forEach(([hubId, memberIds], index) => {
    const r = radii[index];
    place(hubId, memberIds, { x, y: y + r });
    y += 2 * r + spacing * 2;
  });
  return positions;
}

// ── Hover ───────────────────────────────────────────────────────────

/**
 * Extra lines the canvas hover draws under a node's label: for an
 * unresolved node, why, one plain line per flag; for a hub, how to open
 * or close it; nothing otherwise.
 *
 * @param nodeKey - The node's id.
 * @param content - The node's properties bag.
 */
export function hoverDetailLines(nodeKey: string, content: unknown): string[] {
  if (isUnresolvedBucketNode(nodeKey)) return [UNRESOLVED_BUCKET_HINT];
  return nodeFlags(content).map((flag) => flag.reason);
}
