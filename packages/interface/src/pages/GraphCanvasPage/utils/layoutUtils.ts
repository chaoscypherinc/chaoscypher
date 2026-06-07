// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Layout Utilities: Apply different layout algorithms to graph nodes.
 *
 * All algorithms work on cyclic knowledge graphs (not just trees).
 * Operates on plain { id, x, y } objects and returns a PositionMap.
 * The caller applies positions back to the graphology graph.
 */

import { forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide } from 'd3-force';
import type Graph from 'graphology';

export type LayoutNode = { id: string; x: number; y: number; templateId?: string; size?: number; sourceId?: string };
export type LayoutEdge = { id: string; source: string; target: string };
type PositionMap = Map<string, { x: number; y: number }>;

// ── Helpers ──────────────────────────────────────────────────────────

/**
 * Build an adjacency list (undirected) from edges.
 */
function buildAdjacency(
  nodes: LayoutNode[],
  edges: LayoutEdge[],
): Map<string, Set<string>> {
  const adj = new Map<string, Set<string>>();
  for (const n of nodes) adj.set(n.id, new Set());
  for (const e of edges) {
    adj.get(e.source)?.add(e.target);
    adj.get(e.target)?.add(e.source);
  }
  return adj;
}

/**
 * Find the most-connected node.
 */
function findMostConnected(adj: Map<string, Set<string>>, nodes: LayoutNode[]): string {
  let bestId = nodes[0].id;
  let bestDeg = 0;
  for (const [id, neighbors] of adj) {
    if (neighbors.size > bestDeg) {
      bestDeg = neighbors.size;
      bestId = id;
    }
  }
  return bestId;
}

/**
 * BFS from a root, returning level assignments for all reachable nodes.
 * Handles cycles by only visiting each node once.
 */
function bfsLevels(
  root: string,
  adj: Map<string, Set<string>>,
): Map<string, number> {
  const levels = new Map<string, number>();
  const queue: string[] = [root];
  levels.set(root, 0);

  while (queue.length > 0) {
    const nodeId = queue.shift()!;
    const lvl = levels.get(nodeId)!;
    for (const neighbor of adj.get(nodeId) || []) {
      if (!levels.has(neighbor)) {
        levels.set(neighbor, lvl + 1);
        queue.push(neighbor);
      }
    }
  }
  return levels;
}

/**
 * Group node IDs by their level.
 */
function groupByLevel(levels: Map<string, number>): Map<number, string[]> {
  const groups = new Map<number, string[]>();
  for (const [nodeId, level] of levels) {
    if (!groups.has(level)) groups.set(level, []);
    groups.get(level)!.push(nodeId);
  }
  return groups;
}

// ── Source Separation (shared) ──────────────────────────────────────

/**
 * Compute separated center positions for each source document.
 * Returns a map of sourceId → { x, y } center point.
 * Used by all layout algorithms to keep sources visually distinct.
 */
function computeSourceCenters(
  nodes: LayoutNode[],
  edges: LayoutEdge[],
  gap: number,
): Map<string, { x: number; y: number }> {
  const sourceGroups = new Map<string, LayoutNode[]>();
  for (const n of nodes) {
    const key = n.sourceId || '_none';
    if (!sourceGroups.has(key)) sourceGroups.set(key, []);
    sourceGroups.get(key)!.push(n);
  }

  const sourceIds = Array.from(sourceGroups.keys());
  if (sourceIds.length <= 1) {
    // Single source: center at origin
    const centers = new Map<string, { x: number; y: number }>();
    centers.set(sourceIds[0] || '_none', { x: 0, y: 0 });
    return centers;
  }

  const sourceRadii = new Map<string, number>();
  for (const [sid, members] of sourceGroups) {
    sourceRadii.set(sid, Math.max(40, Math.sqrt(members.length) * gap));
  }

  // Build source-level edges
  const nodeSource = new Map<string, string>();
  for (const n of nodes) nodeSource.set(n.id, n.sourceId || '_none');
  const edgePairs = new Set<string>();
  const sourceEdges: { source: string; target: string }[] = [];
  for (const e of edges) {
    const ss = nodeSource.get(e.source);
    const ts = nodeSource.get(e.target);
    if (ss && ts && ss !== ts) {
      const key = ss < ts ? `${ss}|${ts}` : `${ts}|${ss}`;
      if (!edgePairs.has(key)) {
        edgePairs.add(key);
        sourceEdges.push({ source: ss, target: ts });
      }
    }
  }

  const simNodes = sourceIds.map(id => ({
    id,
    x: Math.random() * 200,
    y: Math.random() * 200,
    radius: sourceRadii.get(id) || 40,
  }));

  type SN = (typeof simNodes)[number];
  const sim = forceSimulation(simNodes)
    .force(
      'link',
      forceLink<SN, { source: string; target: string }>(
        sourceEdges.map(e => ({ source: e.source, target: e.target })),
      )
        .id((d) => d.id)
        .distance((d) => {
          const s = d.source as unknown as SN;
          const t = d.target as unknown as SN;
          return s.radius + t.radius + 40;
        })
        .strength(0.4),
    )
    .force('charge', forceManyBody().strength(-6).distanceMax(80))
    .force('center', forceCenter(0, 0).strength(0.20))
    .force('collision', forceCollide<SN>().radius((d) => d.radius + 20).strength(1));

  sim.stop();
  for (let i = 0; i < 300; i++) sim.tick();

  const centers = new Map<string, { x: number; y: number }>();
  for (const sn of simNodes) {
    centers.set(sn.id, { x: sn.x || 0, y: sn.y || 0 });
  }
  return centers;
}

/**
 * Split nodes and edges by source, returning per-source subsets.
 */
function splitBySource(
  nodes: LayoutNode[],
  edges: LayoutEdge[],
): Map<string, { nodes: LayoutNode[]; edges: LayoutEdge[] }> {
  const nodeSourceMap = new Map<string, string>();
  const groups = new Map<string, { nodes: LayoutNode[]; edges: LayoutEdge[] }>();

  for (const n of nodes) {
    const sid = n.sourceId || '_none';
    nodeSourceMap.set(n.id, sid);
    if (!groups.has(sid)) groups.set(sid, { nodes: [], edges: [] });
    groups.get(sid)!.nodes.push(n);
  }

  for (const e of edges) {
    const ss = nodeSourceMap.get(e.source);
    const ts = nodeSourceMap.get(e.target);
    // Assign edge to source if both endpoints are in the same source
    if (ss && ts && ss === ts) {
      groups.get(ss)!.edges.push(e);
    }
  }

  return groups;
}

// ── Force-Directed (D3) ─────────────────────────────────────────────

/**
 * Run force simulation on a single set of nodes/edges.
 */
function forceLayoutSingle(
  nodes: LayoutNode[],
  edges: LayoutEdge[],
  centerX: number,
  centerY: number,
): PositionMap {
  if (nodes.length === 0) return new Map();

  const nodeCount = nodes.length;
  const isLarge = nodeCount >= 500 || edges.length >= 2000;
  const linkDist = isLarge ? 22 : 40;
  const charge = isLarge ? -25 : -65;
  const chargeMax = isLarge ? 160 : 400;

  const simulationNodes = nodes.map(n => ({
    id: n.id,
    x: centerX + (Math.random() - 0.5) * 100,
    y: centerY + (Math.random() - 0.5) * 100,
    nodeSize: n.size ?? 8,
  }));

  const nodeIds = new Set(nodes.map(n => n.id));
  const simulationLinks = edges
    .filter(e => nodeIds.has(e.source) && nodeIds.has(e.target))
    .map(e => ({ source: e.source, target: e.target }));

  type SimNode = (typeof simulationNodes)[number];
  const simulation = forceSimulation(simulationNodes)
    .force(
      'link',
      forceLink<SimNode, { source: string; target: string }>(simulationLinks)
        .id((d) => d.id)
        .distance(linkDist)
        .strength(0.45),
    )
    .force('charge', forceManyBody().strength(charge).distanceMax(chargeMax))
    .force('center', forceCenter(centerX, centerY).strength(0.05))
    .force('collision', forceCollide<SimNode>().radius((d) => d.nodeSize + 2).strength(0.7))
    .alphaDecay(isLarge ? 0.03 : 0.02)
    .velocityDecay(isLarge ? 0.5 : 0.4);

  simulation.stop();
  const iterations = isLarge ? 200 : 300;
  for (let i = 0; i < iterations; i++) simulation.tick();

  const positions: PositionMap = new Map();
  for (const sn of simulationNodes) {
    positions.set(sn.id, { x: sn.x || 0, y: sn.y || 0 });
  }
  return positions;
}

/**
 * Force-directed layout: organic clusters where connected nodes pull together.
 * Multi-source graphs get separated regions; single source works as before.
 */
export function applyForceLayout(
  nodes: LayoutNode[],
  edges: LayoutEdge[],
): PositionMap {
  if (nodes.length === 0) return new Map();

  const sourceCenters = computeSourceCenters(nodes, edges, 11);
  const bySource = splitBySource(nodes, edges);
  const positions: PositionMap = new Map();

  for (const [sourceId, data] of bySource) {
    const center = sourceCenters.get(sourceId) || { x: 0, y: 0 };
    const sourcePositions = forceLayoutSingle(data.nodes, data.edges, center.x, center.y);
    for (const [id, pos] of sourcePositions) positions.set(id, pos);
  }

  return positions;
}

// ── Grid ─────────────────────────────────────────────────────────────

/**
 * Grid layout: even rows and columns.
 * Shape: square grid.
 */
export function applyGridLayout(nodes: LayoutNode[]): PositionMap {
  if (nodes.length === 0) return new Map();

  const cols = Math.ceil(Math.sqrt(nodes.length));
  const cellWidth = 30;
  const cellHeight = 30;

  const positions: PositionMap = new Map();
  nodes.forEach((node, index) => {
    positions.set(node.id, {
      x: (index % cols) * cellWidth,
      y: Math.floor(index / cols) * cellHeight,
    });
  });
  return positions;
}

// ── Mindmap (template clusters) ──────────────────────────────────────

/**
 * Mindmap layout: groups nodes by template type into distinct clusters,
 * then arranges clusters using force simulation. Shows the semantic
 * structure of the knowledge graph.
 * Shape: scattered clusters grouped by type.
 */
export function applyMindmapLayout(
  nodes: LayoutNode[],
  edges: LayoutEdge[],
): PositionMap {
  if (nodes.length === 0) return new Map();

  const gap = 11;

  // ── Level 1: Group nodes by source ────────────────────────────────
  const sourceGroups = new Map<string, LayoutNode[]>();
  for (const n of nodes) {
    const key = n.sourceId || '_none';
    if (!sourceGroups.has(key)) sourceGroups.set(key, []);
    sourceGroups.get(key)!.push(n);
  }

  // Compute each source's overall radius from its node count
  const sourceIds = Array.from(sourceGroups.keys());
  const sourceRadii = new Map<string, number>();
  for (const [sid, members] of sourceGroups) {
    sourceRadii.set(sid, Math.max(40, Math.sqrt(members.length) * gap * 1.0));
  }

  // Position source centers using force simulation so they don't overlap
  const sourceSim = sourceIds.map(id => ({
    id,
    x: Math.random() * 500,
    y: Math.random() * 500,
    radius: sourceRadii.get(id) || 40,
  }));

  // Add edges between sources that share graph edges
  const nodeSource = new Map<string, string>();
  for (const n of nodes) nodeSource.set(n.id, n.sourceId || '_none');
  const sourceEdgePairs = new Set<string>();
  const sourceEdges: { source: string; target: string }[] = [];
  for (const e of edges) {
    const ss = nodeSource.get(e.source);
    const ts = nodeSource.get(e.target);
    if (ss && ts && ss !== ts) {
      const key = ss < ts ? `${ss}|${ts}` : `${ts}|${ss}`;
      if (!sourceEdgePairs.has(key)) {
        sourceEdgePairs.add(key);
        sourceEdges.push({ source: ss, target: ts });
      }
    }
  }

  type SourceNode = (typeof sourceSim)[number];
  const srcSim = forceSimulation(sourceSim)
    .force(
      'link',
      forceLink<SourceNode, { source: string; target: string }>(
        sourceEdges.map(e => ({ source: e.source, target: e.target })),
      )
        .id((d) => d.id)
        .distance((d) => {
          const s = d.source as unknown as SourceNode;
          const t = d.target as unknown as SourceNode;
          return s.radius + t.radius + 40;
        })
        .strength(0.4),
    )
    .force('charge', forceManyBody().strength(-6).distanceMax(80))
    .force('center', forceCenter(0, 0).strength(0.20))
    .force(
      'collision',
      forceCollide<SourceNode>().radius((d) => d.radius + 20).strength(1),
    );

  srcSim.stop();
  for (let i = 0; i < 300; i++) srcSim.tick();

  const sourceCenters = new Map<string, { x: number; y: number }>();
  for (const sn of sourceSim) {
    sourceCenters.set(sn.id, { x: sn.x || 0, y: sn.y || 0 });
  }

  // ── Level 2: Within each source, group by template ────────────────
  const positions: PositionMap = new Map();

  for (const [sourceId, sourceNodes] of sourceGroups) {
    const sourceCenter = sourceCenters.get(sourceId) || { x: 0, y: 0 };

    // Sub-group by template within this source
    const templateGroups = new Map<string, LayoutNode[]>();
    for (const n of sourceNodes) {
      const key = n.templateId || 'default';
      if (!templateGroups.has(key)) templateGroups.set(key, []);
      templateGroups.get(key)!.push(n);
    }

    // Build node→template lookup for this source
    const nodeTemplate = new Map<string, string>();
    for (const n of sourceNodes) nodeTemplate.set(n.id, n.templateId || 'default');

    // Compute cluster radii
    const templateIds = Array.from(templateGroups.keys());
    const clusterInfo = new Map<string, { nodes: LayoutNode[]; radius: number }>();
    for (const [tid, members] of templateGroups) {
      const radius = Math.max(18, Math.sqrt(members.length) * gap * 0.55);
      clusterInfo.set(tid, { nodes: members, radius });
    }

    // Inter-cluster edges within this source
    const interEdges: { source: string; target: string }[] = [];
    const edgePairs = new Set<string>();
    for (const e of edges) {
      const st = nodeTemplate.get(e.source);
      const tt = nodeTemplate.get(e.target);
      if (st && tt && st !== tt) {
        const key = st < tt ? `${st}|${tt}` : `${tt}|${st}`;
        if (!edgePairs.has(key)) {
          edgePairs.add(key);
          interEdges.push({ source: st, target: tt });
        }
      }
    }

    // Position template clusters within this source's region
    const clusterSimNodes = templateIds.map(id => {
      const info = clusterInfo.get(id)!;
      return {
        id,
        x: sourceCenter.x + (Math.random() - 0.5) * 100,
        y: sourceCenter.y + (Math.random() - 0.5) * 100,
        radius: info.radius,
      };
    });
    const clusterSimLinks = interEdges.map(e => ({ source: e.source, target: e.target }));

    type ClusterNode = (typeof clusterSimNodes)[number];
    const clusterSim = forceSimulation(clusterSimNodes)
      .force(
        'link',
        forceLink<ClusterNode, { source: string; target: string }>(clusterSimLinks)
          .id((d) => d.id)
          .distance(80)
          .strength(0.5),
      )
      .force('charge', forceManyBody().strength(-100).distanceMax(300))
      .force('center', forceCenter(sourceCenter.x, sourceCenter.y).strength(0.15))
      .force(
        'collision',
        forceCollide<ClusterNode>().radius((d) => d.radius + 10).strength(0.9),
      );

    clusterSim.stop();
    for (let i = 0; i < 200; i++) clusterSim.tick();

    // Place nodes within each template cluster
    for (const [tid, members] of templateGroups) {
      const csn = clusterSimNodes.find(c => c.id === tid);
      const center = csn ? { x: csn.x || 0, y: csn.y || 0 } : sourceCenter;
      const count = members.length;

      if (count === 1) {
        positions.set(members[0].id, { x: center.x, y: center.y });
        continue;
      }

      // Spiral layout within cluster
      const spacing = gap * 0.7;
      for (let i = 0; i < count; i++) {
        if (i === 0) {
          positions.set(members[i].id, { x: center.x, y: center.y });
          continue;
        }
        const angle = i * 2.4;
        const r = spacing * Math.sqrt(i);
        positions.set(members[i].id, {
          x: center.x + r * Math.cos(angle),
          y: center.y + r * Math.sin(angle),
        });
      }
    }
  }

  return positions;
}

// ── Hierarchical (top-down layered blocks) ───────────────────────────

/**
 * Hierarchical layout: BFS depth layers stacked top-to-bottom.
 * Each layer wraps into a 2D rectangular block (not a single row).
 * Shape: stacked rectangular tiers, wider tiers for deeper levels.
 */
/**
 * Run hierarchical layout for a single set of nodes/edges at a given offset.
 */
function hierarchicalSingle(
  nodes: LayoutNode[],
  edges: LayoutEdge[],
  offsetX: number,
  offsetY: number,
): PositionMap {
  if (nodes.length === 0) return new Map();

  const adj = buildAdjacency(nodes, edges);
  const rootId = findMostConnected(adj, nodes);
  const levels = bfsLevels(rootId, adj);

  const maxReached = levels.size > 0 ? Math.max(...levels.values()) : 0;
  for (const n of nodes) {
    if (!levels.has(n.id)) levels.set(n.id, maxReached + 1);
  }

  const byLevel = groupByLevel(levels);
  const maxLevel = Math.max(...byLevel.keys(), 0);

  const gap = 15;
  const tierGap = 40;
  const positions: PositionMap = new Map();
  let yOffset = 0;

  for (let level = 0; level <= maxLevel; level++) {
    const nodeIds = byLevel.get(level) || [];
    const count = nodeIds.length;
    if (count === 0) continue;

    const cols = Math.max(1, Math.ceil(Math.sqrt(count * 3)));
    const rows = Math.ceil(count / cols);
    const blockWidth = (cols - 1) * gap;

    for (let i = 0; i < count; i++) {
      const col = i % cols;
      const row = Math.floor(i / cols);
      positions.set(nodeIds[i], {
        x: offsetX + -(blockWidth / 2) + col * gap,
        y: offsetY + yOffset + row * gap,
      });
    }

    yOffset += rows * gap + tierGap;
  }

  return positions;
}

export function applyHierarchicalLayout(
  nodes: LayoutNode[],
  edges: LayoutEdge[],
): PositionMap {
  if (nodes.length === 0) return new Map();

  const sourceCenters = computeSourceCenters(nodes, edges, 11);
  const bySource = splitBySource(nodes, edges);
  const positions: PositionMap = new Map();

  for (const [sourceId, data] of bySource) {
    const center = sourceCenters.get(sourceId) || { x: 0, y: 0 };
    const sourcePositions = hierarchicalSingle(data.nodes, data.edges, center.x, center.y);
    for (const [id, pos] of sourcePositions) positions.set(id, pos);
  }

  return positions;
}

// ── Radial (concentric rings) ────────────────────────────────────────

/**
 * Radial layout: concentric rings radiating from the most-connected node.
 * Shape: bullseye / target pattern.
 */
/**
 * Run radial layout for a single set of nodes/edges at a given center.
 */
function radialSingle(
  nodes: LayoutNode[],
  edges: LayoutEdge[],
  centerX: number,
  centerY: number,
): PositionMap {
  if (nodes.length === 0) return new Map();

  const adj = buildAdjacency(nodes, edges);
  const centerId = findMostConnected(adj, nodes);
  const levels = bfsLevels(centerId, adj);

  const maxLevel = levels.size > 0 ? Math.max(...levels.values()) : 0;
  for (const n of nodes) {
    if (!levels.has(n.id)) levels.set(n.id, maxLevel + 1);
  }

  const byLevel = groupByLevel(levels);
  const finalMaxLevel = Math.max(...byLevel.keys(), 0);

  const MIN_NODE_GAP = 10;
  const BASE_RING_GAP = 20;

  const positions: PositionMap = new Map();
  for (let level = 0; level <= finalMaxLevel; level++) {
    const nodeIds = byLevel.get(level) || [];
    if (level === 0) {
      positions.set(nodeIds[0], { x: centerX, y: centerY });
      continue;
    }

    const count = nodeIds.length;
    const requiredCircumference = MIN_NODE_GAP * count;
    const minRadius = requiredCircumference / (2 * Math.PI);
    const baseRadius = level * BASE_RING_GAP;
    const radius = Math.max(minRadius, baseRadius);

    for (let i = 0; i < count; i++) {
      const angle = (i / count) * 2 * Math.PI;
      positions.set(nodeIds[i], {
        x: centerX + radius * Math.cos(angle),
        y: centerY + radius * Math.sin(angle),
      });
    }
  }

  return positions;
}

export function applyRadialLayout(
  nodes: LayoutNode[],
  edges: LayoutEdge[],
): PositionMap {
  if (nodes.length === 0) return new Map();

  const sourceCenters = computeSourceCenters(nodes, edges, 11);
  const bySource = splitBySource(nodes, edges);
  const positions: PositionMap = new Map();

  for (const [sourceId, data] of bySource) {
    const center = sourceCenters.get(sourceId) || { x: 0, y: 0 };
    const sourcePositions = radialSingle(data.nodes, data.edges, center.x, center.y);
    for (const [id, pos] of sourcePositions) positions.set(id, pos);
  }

  return positions;
}

// ── Apply to graph ──────────────────────────────────────────────────

/**
 * Apply a PositionMap back to a graphology graph.
 */
export function applyPositionsToGraph(
  graph: Graph,
  positions: PositionMap,
): void {
  for (const [id, pos] of positions) {
    if (graph.hasNode(id)) {
      graph.setNodeAttribute(id, 'x', pos.x);
      graph.setNodeAttribute(id, 'y', pos.y);
    }
  }
}
