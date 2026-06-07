// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Shared constellation layout pipeline.
 *
 * Pure (framework-agnostic) d3-force layout used by both the DashboardGraph
 * decorative background and the per-source Knowledge map on the Overview tab.
 * Given raw node/edge ids it samples a connected subgraph (when over a cap),
 * runs a two-level mindmap layout (cluster by source, then by template),
 * normalizes the bounding box, and emits positioned ``GraphNode`` /
 * ``GraphEdge`` records ready for Canvas rendering.
 *
 * Extracted verbatim from the original ``DashboardPage/useGraphData`` so the
 * dashboard's look is unchanged; the per-source preview reuses it with a
 * different cap / cache key and keeps orphan nodes.
 */

import {
  forceSimulation,
  forceLink,
  forceManyBody,
  forceCenter,
  forceCollide,
  forceX,
  forceY,
  type SimulationNodeDatum,
  type SimulationLinkDatum,
} from 'd3-force';
import { ChaosCypherPalette } from '../../theme/palette';

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/** Positioned node ready for Canvas rendering. */
export interface GraphNode {
  id: string;
  x: number;
  y: number;
  radius: number;
  color: string;
  opacity: number;
  /**
   * Structural depth in [-0.5, 0.5], populated only when `assignDepth` is set.
   * Lets a renderer layer whole clusters front-to-back for real 3D parallax
   * instead of the per-node hash jitter from `nodeDepth`. Absent for consumers
   * (e.g. the per-source map) that don't opt in.
   */
  z?: number;
}

/** Positioned edge ready for Canvas rendering. */
export interface GraphEdge {
  source: string;
  target: string;
  color: string;
  opacity: number;
}

/** Minimal raw node the layout needs (template/source drive clustering + color). */
export interface RawNode {
  id: string;
  template_id?: string;
  source_id?: string;
}

/** Minimal raw edge the layout needs. */
export interface RawEdge {
  source_node_id: string;
  target_node_id: string;
}

export interface ConstellationLayoutOptions {
  /** Cap on rendered nodes; over this, a connected BFS sample is taken. */
  maxRenderNodes: number;
  /** sessionStorage key for layout caching (must differ per consumer). */
  cacheKey: string;
  /** Target layout extent X (layout units). Default 600. */
  layoutTargetX?: number;
  /** Target layout extent Y (layout units). Default 400. */
  layoutTargetY?: number;
  /**
   * Drop nodes with no surviving edges. The dashboard drops orphans (they
   * clutter the decorative view); the per-source map keeps them so the
   * constellation faithfully reflects the source's entities. Default true.
   */
  dropOrphans?: boolean;
  /**
   * Settle individual nodes with a force pass (connected nodes pull together,
   * anchored to their template-cluster centre) instead of dropping them onto a
   * fixed spiral. Yields short edges and organic cluster shapes. The dashboard
   * opts in; the per-source map keeps the spiral. Default false.
   */
  organicRelax?: boolean;
  /**
   * Emit a structural per-cluster `z` depth on each node (see `GraphNode.z`).
   * Default false — only the dashboard opts in.
   */
  assignDepth?: boolean;
  /**
   * Multiplier on inter-source spacing. `< 1` pulls source clusters in from the
   * corners so edges aren't canvas-spanning straight lines. No effect for a
   * single-source layout (no inter-source forces). Default 1.
   */
  clusterSpread?: number;
}

// ---------------------------------------------------------------------------
// Color
// ---------------------------------------------------------------------------

const NODE_COLORS = [
  ChaosCypherPalette.primary,
  ChaosCypherPalette.secondary,
  ChaosCypherPalette.accent,
  ChaosCypherPalette.purple,
  ChaosCypherPalette.success,
  ChaosCypherPalette.info,
];

/** Deterministic color from a string (template_id or node id). */
export function colorFromId(id: string): string {
  let hash = 0;
  for (let i = 0; i < id.length; i++) {
    hash = (hash * 31 + id.charCodeAt(i)) | 0;
  }
  return NODE_COLORS[Math.abs(hash) % NODE_COLORS.length];
}

// ---------------------------------------------------------------------------
// Connected-cluster sampling
// ---------------------------------------------------------------------------

/**
 * Sample up to `limit` nodes by walking connected clusters via BFS.
 *
 * Starts from a random node, walks outward collecting neighbors so that
 * sampled nodes form connected sub-graphs (maximizing surviving edges).
 * When a component is exhausted, jumps to another random unvisited node.
 */
export function sampleConnected(
  nodes: RawNode[],
  edges: RawEdge[],
  limit: number,
): RawNode[] {
  if (nodes.length <= limit) return nodes;

  // Build adjacency list
  const adj = new Map<string, string[]>();
  for (const e of edges) {
    let list = adj.get(e.source_node_id);
    if (!list) { list = []; adj.set(e.source_node_id, list); }
    list.push(e.target_node_id);

    list = adj.get(e.target_node_id);
    if (!list) { list = []; adj.set(e.target_node_id, list); }
    list.push(e.source_node_id);
  }

  const nodeMap = new Map(nodes.map((n) => [n.id, n]));
  const visited = new Set<string>();
  const result: RawNode[] = [];

  // Shuffled start indices so each load looks different
  const indices = Array.from({ length: nodes.length }, (_, i) => i);
  for (let i = indices.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [indices[i], indices[j]] = [indices[j], indices[i]];
  }

  let startIdx = 0;

  while (result.length < limit && startIdx < indices.length) {
    // Find next unvisited node
    while (startIdx < indices.length && visited.has(nodes[indices[startIdx]].id)) {
      startIdx++;
    }
    if (startIdx >= indices.length) break;

    // BFS from this node
    const queue = [nodes[indices[startIdx]].id];
    visited.add(queue[0]);

    while (queue.length > 0 && result.length < limit) {
      const id = queue.shift()!;
      const node = nodeMap.get(id);
      if (node) result.push(node);

      // Shuffle neighbors for variety, then enqueue unvisited
      const neighbors = adj.get(id) || [];
      for (let i = neighbors.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [neighbors[i], neighbors[j]] = [neighbors[j], neighbors[i]];
      }
      for (const nid of neighbors) {
        if (!visited.has(nid)) {
          visited.add(nid);
          queue.push(nid);
        }
      }
    }

    startIdx++;
  }

  return result;
}

// ---------------------------------------------------------------------------
// Two-level mindmap layout (cluster by source, then template)
// ---------------------------------------------------------------------------

interface SimNode extends SimulationNodeDatum {
  id: string;
  templateId: string;
  sourceId: string;
  /** Anchor (template-cluster centre) the organic relax pull is biased toward. */
  ax?: number;
  ay?: number;
  /** Structural depth in [-0.5, 0.5], set when `assignDepth` is requested. */
  z?: number;
}

interface SimLink extends SimulationLinkDatum<SimNode> {
  source: string | SimNode;
  target: string | SimNode;
}

interface MindmapOptions {
  /** Multiplier on inter-source spacing (`< 1` tightens). */
  clusterSpread: number;
  /** Settle nodes with a force pass instead of the fixed spiral. */
  organicRelax: boolean;
  /** Populate `SimNode.z` with a structural per-cluster depth. */
  assignDepth: boolean;
}

/** Two-level mindmap layout: cluster by source, then by template within each source. */
function applyMindmapLayout(
  simNodes: SimNode[],
  simLinks: SimLink[],
  mindmapOpts: MindmapOptions,
): void {
  if (simNodes.length === 0) return;

  const { clusterSpread, organicRelax, assignDepth } = mindmapOpts;
  const gap = 16;

  // ── Level 1: Group nodes by source ──────────────────────────────
  const sourceGroups = new Map<string, SimNode[]>();
  for (const n of simNodes) {
    const key = n.sourceId;
    if (!sourceGroups.has(key)) sourceGroups.set(key, []);
    sourceGroups.get(key)!.push(n);
  }

  const sourceIds = Array.from(sourceGroups.keys());
  const sourceRadii = new Map<string, number>();
  for (const [sid, members] of sourceGroups) {
    sourceRadii.set(sid, Math.max(50, Math.sqrt(members.length) * gap * 1.2));
  }

  // Position source centers using force simulation
  const sourceSim = sourceIds.map((id) => ({
    id,
    x: Math.random() * 500,
    y: Math.random() * 500,
    radius: sourceRadii.get(id) || 40,
  }));

  // Build inter-source edges
  const nodeSource = new Map<string, string>();
  for (const n of simNodes) nodeSource.set(n.id, n.sourceId);
  const sourceEdgePairs = new Set<string>();
  const sourceEdges: { source: string; target: string }[] = [];
  for (const l of simLinks) {
    const sid = typeof l.source === 'string' ? l.source : l.source.id;
    const tid = typeof l.target === 'string' ? l.target : l.target.id;
    const ss = nodeSource.get(sid);
    const ts = nodeSource.get(tid);
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
        sourceEdges.map((e) => ({ source: e.source, target: e.target })),
      )
        .id((d) => d.id)
        .distance((d) => {
          const s = d.source as unknown as SourceNode;
          const t = d.target as unknown as SourceNode;
          return s.radius + t.radius + 300 * clusterSpread;
        })
        .strength(0.15),
    )
    .force('charge', forceManyBody().strength(-800 * clusterSpread).distanceMax(1500))
    .force('center', forceCenter(0, 0).strength(0.015))
    .force(
      'collision',
      forceCollide<SourceNode>().radius((d) => d.radius + 150 * clusterSpread).strength(1),
    );

  srcSim.stop();
  for (let i = 0; i < 120; i++) srcSim.tick();

  const sourceCenters = new Map<string, { x: number; y: number }>();
  for (const sn of sourceSim) {
    sourceCenters.set(sn.id, { x: sn.x || 0, y: sn.y || 0 });
  }

  // ── Level 2: Within each source, group by template ──────────────
  for (const [sourceId, sourceNodes] of sourceGroups) {
    const sourceCenter = sourceCenters.get(sourceId) || { x: 0, y: 0 };

    const templateGroups = new Map<string, SimNode[]>();
    for (const n of sourceNodes) {
      const key = n.templateId;
      if (!templateGroups.has(key)) templateGroups.set(key, []);
      templateGroups.get(key)!.push(n);
    }

    const nodeTemplate = new Map<string, string>();
    for (const n of sourceNodes) nodeTemplate.set(n.id, n.templateId);

    const templateIds = Array.from(templateGroups.keys());
    const clusterInfo = new Map<string, { nodes: SimNode[]; radius: number }>();
    for (const [tid, members] of templateGroups) {
      const radius = Math.max(25, Math.sqrt(members.length) * gap * 0.7);
      clusterInfo.set(tid, { nodes: members, radius });
    }

    // Inter-cluster edges within this source
    const interEdges: { source: string; target: string }[] = [];
    const edgePairs = new Set<string>();
    for (const l of simLinks) {
      const sid = typeof l.source === 'string' ? l.source : l.source.id;
      const tid = typeof l.target === 'string' ? l.target : l.target.id;
      const st = nodeTemplate.get(sid);
      const tt = nodeTemplate.get(tid);
      if (st && tt && st !== tt) {
        const key = st < tt ? `${st}|${tt}` : `${tt}|${st}`;
        if (!edgePairs.has(key)) {
          edgePairs.add(key);
          interEdges.push({ source: st, target: tt });
        }
      }
    }

    // Position template clusters within this source's region
    const clusterSimNodes = templateIds.map((id) => {
      const info = clusterInfo.get(id)!;
      return {
        id,
        x: sourceCenter.x + (Math.random() - 0.5) * 100,
        y: sourceCenter.y + (Math.random() - 0.5) * 100,
        radius: info.radius,
      };
    });

    type ClusterNode = (typeof clusterSimNodes)[number];
    const clusterSim = forceSimulation(clusterSimNodes)
      .force(
        'link',
        forceLink<ClusterNode, { source: string; target: string }>(
          interEdges.map((e) => ({ source: e.source, target: e.target })),
        )
          .id((d) => d.id)
          .distance(100)
          .strength(0.4),
      )
      .force('charge', forceManyBody().strength(-150).distanceMax(400))
      .force('center', forceCenter(sourceCenter.x, sourceCenter.y).strength(0.12))
      .force(
        'collision',
        forceCollide<ClusterNode>().radius((d) => d.radius + 18).strength(0.9),
      );

    clusterSim.stop();
    for (let i = 0; i < 80; i++) clusterSim.tick();

    // Place nodes within each template cluster. Every node records its cluster
    // centre as an anchor (`ax`/`ay`); organic relax pulls toward it while the
    // spiral path snaps straight to it.
    for (const [tid, members] of templateGroups) {
      const csn = clusterSimNodes.find((c) => c.id === tid);
      const center = csn ? { x: csn.x || 0, y: csn.y || 0 } : sourceCenter;
      const spacing = gap * 0.9;

      for (let i = 0; i < members.length; i++) {
        members[i].ax = center.x;
        members[i].ay = center.y;

        if (organicRelax) {
          // Seed near the centre; the global force pass settles real positions.
          members[i].x = center.x + (Math.random() - 0.5) * spacing;
          members[i].y = center.y + (Math.random() - 0.5) * spacing;
          continue;
        }

        // Fixed phyllotaxis spiral (the per-source map's established look).
        if (i === 0) {
          members[i].x = center.x;
          members[i].y = center.y;
          continue;
        }
        const angle = i * 2.4;
        const r = spacing * Math.sqrt(i);
        members[i].x = center.x + r * Math.cos(angle);
        members[i].y = center.y + r * Math.sin(angle);
      }
    }
  }

  // ── Organic relax: settle real nodes by their real edges ─────────
  // Connected nodes pull together (short edges, no spokes) while a positional
  // anchor keeps each node near its template cluster so the structure holds.
  if (organicRelax) {
    const relax = forceSimulation(simNodes)
      .force(
        'link',
        forceLink<SimNode, SimLink>(simLinks)
          .id((d) => d.id)
          .distance(gap * 1.3)
          .strength(0.35),
      )
      .force('charge', forceManyBody<SimNode>().strength(-22).distanceMax(140))
      .force('collision', forceCollide<SimNode>().radius(gap * 0.55).strength(0.85))
      .force('anchorX', forceX<SimNode>((d) => d.ax ?? 0).strength(0.07))
      .force('anchorY', forceY<SimNode>((d) => d.ay ?? 0).strength(0.07));

    relax.stop();
    for (let i = 0; i < 240; i++) relax.tick();
  }

  // ── Structural depth: layer whole clusters front-to-back ─────────
  if (assignDepth) {
    assignClusterDepth(sourceGroups);
  }
}

/**
 * Assign each node a structural `z` in [-0.5, 0.5] so clusters occupy distinct
 * depth bands: sources spread across the full range, template clusters get a
 * smaller sub-offset within their source, plus a little per-node jitter. This
 * is what turns a slow rotation into real parallax instead of a tilting sheet.
 */
function assignClusterDepth(sourceGroups: Map<string, SimNode[]>): void {
  const sourceIds = Array.from(sourceGroups.keys());
  const multiSource = sourceIds.length > 1;

  sourceIds.forEach((sid, si) => {
    const members = sourceGroups.get(sid)!;
    const base = multiSource ? si / (sourceIds.length - 1) - 0.5 : 0;

    const templateIds = Array.from(new Set(members.map((m) => m.templateId)));
    const multiTemplate = templateIds.length > 1;
    const templateIndex = new Map(templateIds.map((t, i) => [t, i]));

    for (const n of members) {
      const ti = templateIndex.get(n.templateId) ?? 0;
      const templateOffset = multiTemplate ? (ti / (templateIds.length - 1) - 0.5) * 0.22 : 0;
      const jitter = (hashUnit(n.id) - 0.5) * 0.12;
      n.z = Math.max(-0.5, Math.min(0.5, base * 0.8 + templateOffset + jitter));
    }
  });
}

/** Deterministic hash of a string id to a value in [0, 1). */
function hashUnit(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) {
    h = (h * 31 + id.charCodeAt(i)) | 0;
  }
  return (Math.abs(h) % 10000) / 10000;
}

// ---------------------------------------------------------------------------
// Bounding-box normalization
// ---------------------------------------------------------------------------

/**
 * Center and uniformly rescale sim-node positions to a target bounding box.
 *
 * Force-layout output is unbounded — with many sources and strong repulsion
 * the extent can exceed the viewport. This re-fits the whole graph to a
 * predictable range so the canvas never renders offscreen.
 */
function normalizeLayout(simNodes: SimNode[], targetRangeX: number, targetRangeY: number): void {
  if (simNodes.length === 0) return;

  let xMin = Infinity, xMax = -Infinity, yMin = Infinity, yMax = -Infinity;
  for (const n of simNodes) {
    const nx = n.x ?? 0;
    const ny = n.y ?? 0;
    if (nx < xMin) xMin = nx;
    if (nx > xMax) xMax = nx;
    if (ny < yMin) yMin = ny;
    if (ny > yMax) yMax = ny;
  }

  const xRange = xMax - xMin;
  const yRange = yMax - yMin;
  const cx = (xMin + xMax) / 2;
  const cy = (yMin + yMax) / 2;

  // Pick the tighter of the two axes so we don't overflow either dimension.
  const scaleX = xRange > 0 ? targetRangeX / xRange : Infinity;
  const scaleY = yRange > 0 ? targetRangeY / yRange : Infinity;
  const scale = Math.min(scaleX, scaleY);
  if (!Number.isFinite(scale)) return;

  for (const n of simNodes) {
    n.x = ((n.x ?? 0) - cx) * scale;
    n.y = ((n.y ?? 0) - cy) * scale;
  }
}

// ---------------------------------------------------------------------------
// sessionStorage layout cache
// ---------------------------------------------------------------------------

interface CachedLayout {
  version: string;
  positions: Record<string, { x: number; y: number; z?: number }>;
}

/** Build a cache version key from node ids + edge count. */
function buildCacheVersion(nodeIds: string[], edgeCount: number): string {
  let hash = 0;
  for (const id of nodeIds) {
    for (let i = 0; i < id.length; i++) {
      hash = (hash * 31 + id.charCodeAt(i)) | 0;
    }
  }
  return `${nodeIds.length}_${edgeCount}_${hash}`;
}

function loadCachedLayout(
  cacheKey: string,
  version: string,
): Record<string, { x: number; y: number; z?: number }> | null {
  try {
    const raw = sessionStorage.getItem(cacheKey);
    if (!raw) return null;
    const cached: CachedLayout = JSON.parse(raw);
    if (cached.version !== version) return null;
    return cached.positions;
  } catch {
    return null;
  }
}

function saveCachedLayout(
  cacheKey: string,
  version: string,
  positions: Record<string, { x: number; y: number; z?: number }>,
): void {
  try {
    const data: CachedLayout = { version, positions };
    sessionStorage.setItem(cacheKey, JSON.stringify(data));
  } catch {
    // Storage full or unavailable — skip silently
  }
}

// ---------------------------------------------------------------------------
// Sim → GraphNode transform
// ---------------------------------------------------------------------------

/** Convert positioned sim nodes to GraphNode format (size/opacity by count). */
function buildGraphNodes(simNodes: SimNode[], nodeCount: number): GraphNode[] {
  const baseRadius =
    nodeCount <= 50 ? 5 : nodeCount <= 100 ? 3.5 : nodeCount <= 200 ? 2.8 : 2.2;
  const baseOpacity =
    nodeCount <= 50 ? 0.55 : nodeCount <= 100 ? 0.4 : nodeCount <= 200 ? 0.32 : 0.25;

  // Slight deterministic variation for organic feel
  return simNodes.map((n, i) => {
    const nx = n.x ?? 0;
    const ny = n.y ?? 0;
    const variation = 0.8 + 0.4 * (((i * 7919) % 97) / 97);

    return {
      id: n.id,
      x: nx,
      y: ny,
      radius: baseRadius * variation,
      color: colorFromId(n.templateId),
      opacity: baseOpacity * (0.6 + variation * 0.4),
      ...(n.z !== undefined ? { z: n.z } : {}),
    };
  });
}

// ---------------------------------------------------------------------------
// Public pipeline
// ---------------------------------------------------------------------------

/**
 * Sample (if needed), lay out, cache, and transform raw nodes/edges into
 * positioned ``GraphNode`` / ``GraphEdge`` records for Canvas rendering.
 */
export function computeConstellationLayout(
  rawNodes: RawNode[],
  rawEdges: RawEdge[],
  opts: ConstellationLayoutOptions,
): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const {
    maxRenderNodes,
    cacheKey,
    layoutTargetX = 600,
    layoutTargetY = 400,
    dropOrphans = true,
    organicRelax = false,
    assignDepth = false,
    clusterSpread = 1,
  } = opts;

  if (rawNodes.length === 0) return { nodes: [], edges: [] };

  // Sample connected clusters (preserves edges) when over the cap.
  const sampledNodes = sampleConnected(rawNodes, rawEdges, maxRenderNodes);
  const sampledIds = new Set(sampledNodes.map((n) => n.id));

  // Only keep edges where both endpoints are in the sample.
  const sampledEdges = rawEdges.filter(
    (e) => sampledIds.has(e.source_node_id) && sampledIds.has(e.target_node_id),
  );

  // Optionally drop orphan nodes (no edges).
  let renderNodes = sampledNodes;
  if (dropOrphans) {
    const connectedIds = new Set<string>();
    for (const e of sampledEdges) {
      connectedIds.add(e.source_node_id);
      connectedIds.add(e.target_node_id);
    }
    renderNodes = sampledNodes.filter((n) => connectedIds.has(n.id));
  }

  if (renderNodes.length === 0) return { nodes: [], edges: [] };

  // Prepare simulation data.
  const simNodes: SimNode[] = renderNodes.map((n) => ({
    id: n.id,
    templateId: n.template_id || n.id,
    sourceId: n.source_id || '_none',
  }));
  const simLinks: SimLink[] = sampledEdges.map((e) => ({
    source: e.source_node_id,
    target: e.target_node_id,
  }));

  // Check layout cache (keyed by node-set + edge count).
  const sortedIds = renderNodes.map((n) => n.id).sort();
  const cacheVersion = buildCacheVersion(sortedIds, sampledEdges.length);
  const cachedPositions = loadCachedLayout(cacheKey, cacheVersion);

  if (cachedPositions) {
    for (const node of simNodes) {
      const pos = cachedPositions[node.id];
      if (pos) {
        node.x = pos.x;
        node.y = pos.y;
        if (pos.z !== undefined) node.z = pos.z;
      }
    }
  } else {
    applyMindmapLayout(simNodes, simLinks, { clusterSpread, organicRelax, assignDepth });
    normalizeLayout(simNodes, layoutTargetX, layoutTargetY);

    const positions: Record<string, { x: number; y: number; z?: number }> = {};
    for (const node of simNodes) {
      positions[node.id] = {
        x: node.x ?? 0,
        y: node.y ?? 0,
        ...(node.z !== undefined ? { z: node.z } : {}),
      };
    }
    saveCachedLayout(cacheKey, cacheVersion, positions);
  }

  const nodeCount = simNodes.length;
  const graphNodes = buildGraphNodes(simNodes, nodeCount);
  const nodeMap = new Map(graphNodes.map((n) => [n.id, n]));

  // Convert to GraphEdge format — fade opacity by length.
  const graphEdges: GraphEdge[] = [];
  for (const l of simLinks) {
    const sourceId = typeof l.source === 'string' ? l.source : l.source.id;
    const targetId = typeof l.target === 'string' ? l.target : l.target.id;
    const sn = nodeMap.get(sourceId);
    const tn = nodeMap.get(targetId);
    if (!sn || !tn) continue;

    const dx = sn.x - tn.x;
    const dy = sn.y - tn.y;
    const dist = Math.sqrt(dx * dx + dy * dy);

    const fade = 1 - Math.min(dist / 400, 1);
    const opacity = 0.03 + fade * fade * 0.15;

    graphEdges.push({
      source: sourceId,
      target: targetId,
      color: sn.color || ChaosCypherPalette.primary,
      opacity,
    });
  }

  return { nodes: graphNodes, edges: graphEdges };
}
