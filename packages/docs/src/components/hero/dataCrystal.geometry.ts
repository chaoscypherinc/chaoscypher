/**
 * Geometry for the dashboard "data crystal" — an icosahedron rendered as the
 * ChaosCypher logo in three dimensions — plus the deterministic mapping that
 * places each real graph node onto its own orbital ring around the crystal.
 *
 * Pure module (no React, no canvas) so the geometry can be unit-tested.
 */

export type Vec3 = [number, number, number];

const PHI = (1 + Math.sqrt(5)) / 2;

// ---------------------------------------------------------------------------
// Icosahedron
// ---------------------------------------------------------------------------

/** The 12 icosahedron vertices (golden-ratio coordinates), pre-normalization. */
const RAW_VERTICES: Vec3[] = [
  [0, 1, PHI], [0, 1, -PHI], [0, -1, PHI], [0, -1, -PHI],
  [1, PHI, 0], [1, -PHI, 0], [-1, PHI, 0], [-1, -PHI, 0],
  [PHI, 0, 1], [PHI, 0, -1], [-PHI, 0, 1], [-PHI, 0, -1],
];

/**
 * Builds the icosahedron: 12 vertices normalized to unit circumradius and the
 * 30 edges connecting nearest-neighbour vertex pairs.
 *
 * In the raw golden-ratio coordinates every edge has squared length exactly 4,
 * so a threshold just above that captures the 30 true edges and nothing else.
 */
export function createIcosahedron(): { vertices: Vec3[]; edges: Array<[number, number]> } {
  const r = Math.hypot(RAW_VERTICES[0][0], RAW_VERTICES[0][1], RAW_VERTICES[0][2]);
  const vertices: Vec3[] = RAW_VERTICES.map((v) => [v[0] / r, v[1] / r, v[2] / r]);

  const edges: Array<[number, number]> = [];
  for (let i = 0; i < RAW_VERTICES.length; i++) {
    for (let j = i + 1; j < RAW_VERTICES.length; j++) {
      const dx = RAW_VERTICES[i][0] - RAW_VERTICES[j][0];
      const dy = RAW_VERTICES[i][1] - RAW_VERTICES[j][1];
      const dz = RAW_VERTICES[i][2] - RAW_VERTICES[j][2];
      if (dx * dx + dy * dy + dz * dz < 4.2) edges.push([i, j]);
    }
  }
  return { vertices, edges };
}

// ---------------------------------------------------------------------------
// Orbital shell mapping
// ---------------------------------------------------------------------------

/** Shell radius (crystal-circumradius units) the orbiting data sits on. */
export const SHELL_RADIUS = 1.8;            // hero: tighter than the dashboard's 2.0
/** Radial spread so per-source depth bands (node.z) layer the shell front/back. */
export const SHELL_DEPTH_SPREAD = 0.45;     // hero: was 0.5

/** Longitude/latitude span the normalized layout maps across the shell. */
const LON_SPAN = Math.PI; // x → a full ring of longitude
const LAT_SPAN = Math.PI * 0.45; // y → most of pole-to-pole, poles excluded

/** A node's position from the d3-force constellation layout. */
export interface OrbitNodeInput {
  x: number;
  y: number;
  z?: number;
}

/**
 * Maps every node to a fixed "home" on a sphere shell around the crystal,
 * preserving the layout's clustering so each source reads as its own clump.
 *
 * The constellation layout already groups nodes by source — compact per-source
 * regions in x/y plus a per-source depth band in z. Normalizing x/y to a shared
 * longitude/latitude lands each source's nodes in the same patch of the shell
 * (a clump); z layers that clump radially. The renderer spins the whole shell as
 * one body, so the clumps orbit the crystal together while staying grouped.
 * Pure + deterministic.
 */
export function computeOrbitHomes(nodes: ReadonlyArray<OrbitNodeInput>): Vec3[] {
  if (nodes.length === 0) return [];

  let xMin = Infinity, xMax = -Infinity, yMin = Infinity, yMax = -Infinity;
  for (const n of nodes) {
    if (n.x < xMin) xMin = n.x;
    if (n.x > xMax) xMax = n.x;
    if (n.y < yMin) yMin = n.y;
    if (n.y > yMax) yMax = n.y;
  }
  const cx = (xMin + xMax) / 2;
  const cy = (yMin + yMax) / 2;
  const halfX = (xMax - xMin) / 2 || 1; // guard a degenerate (single-node) extent
  const halfY = (yMax - yMin) / 2 || 1;

  return nodes.map((n): Vec3 => {
    const nx = Math.max(-1, Math.min(1, (n.x - cx) / halfX));
    const ny = Math.max(-1, Math.min(1, (n.y - cy) / halfY));
    const lon = nx * LON_SPAN;
    const lat = ny * LAT_SPAN;
    const radius = SHELL_RADIUS + (n.z ?? 0) * SHELL_DEPTH_SPREAD;
    const cosLat = Math.cos(lat);
    return [
      radius * cosLat * Math.sin(lon),
      radius * Math.sin(lat),
      radius * cosLat * Math.cos(lon),
    ];
  });
}
