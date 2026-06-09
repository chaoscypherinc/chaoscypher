// Static, deterministic stand-in for the live dashboard graph. One clump per
// "source", entities hanging off a source hub, and NO cross-source edges
// (real sources are separate documents — they don't wire across the graph).

export interface HeroNode {
  id: string;          // `${groupIndex}_${i}` — prefix encodes the source
  x: number;
  y: number;
  z: number;
  color: string;
  radius: number;
  opacity: number;
}

export interface HeroEdge {
  source: string;
  target: string;
  color: string;
  opacity: number;
}

/** Deterministic seeded PRNG (no Math.random — identical every build). */
function seeded(seed: number): () => number {
  let s = seed;
  return () => {
    s = Math.sin(s * 12.9898 + 78.233) * 43758.5453;
    return s - Math.floor(s);
  };
}

interface GroupDef {
  c: string;
  ctr: [number, number];
  back?: boolean;       // smaller, deeper, fainter "satellite" group
}

const GROUPS: GroupDef[] = [
  { c: '#00e5ff', ctr: [-210, -110] }, { c: '#ff2d95', ctr: [-70, -115] },
  { c: '#7c4dff', ctr: [70, -110] },   { c: '#1de9b6', ctr: [210, -115] },
  { c: '#39ff14', ctr: [-210, 110] },  { c: '#ff6d00', ctr: [-70, 115] },
  { c: '#ffd400', ctr: [70, 110] },    { c: '#2979ff', ctr: [210, 115] },
  { c: '#18ffff', ctr: [-300, 0], back: true },   { c: '#d500f9', ctr: [300, 5], back: true },
  { c: '#76ff03', ctr: [-160, -180], back: true }, { c: '#ff4081', ctr: [160, 180], back: true },
  { c: '#536dfe', ctr: [160, -185], back: true },
];

export function buildHeroGraph(): { nodes: HeroNode[]; edges: HeroEdge[] } {
  const rnd = seeded(7);
  const nodes: HeroNode[] = [];
  const edges: HeroEdge[] = [];

  GROUPS.forEach((g, k) => {
    const back = !!g.back;
    const zBand = back ? 0.6 + rnd() * 0.5 : rnd() - 0.5;
    const count = back ? 7 + Math.floor(rnd() * 4) : 14 + Math.floor(rnd() * 12);
    const spreadX = back ? 30 : 42;
    const spreadY = back ? 26 : 34;

    for (let i = 0; i < count; i++) {
      const gx = (rnd() + rnd() + rnd() - 1.5) * spreadX;
      const gy = (rnd() + rnd() + rnd() - 1.5) * spreadY;
      const isHub = i === 0;
      nodes.push({
        id: `${k}_${i}`,
        x: g.ctr[0] + (isHub ? 0 : gx),
        y: g.ctr[1] + (isHub ? 0 : gy),
        z: zBand + (rnd() - 0.5) * 0.25,
        color: g.c,
        radius: isHub ? (back ? 4 : 6.5) : (back ? 1.4 : 2.4) + rnd() * (back ? 1.8 : 3.2),
        opacity: isHub ? (back ? 0.7 : 1) : (back ? 0.4 : 0.6) + rnd() * 0.35,
      });
      // intra-source edges only
      if (back) {
        if (!isHub && rnd() < 0.5) edges.push({ source: `${k}_${i}`, target: `${k}_0`, color: g.c, opacity: 0.22 + rnd() * 0.18 });
      } else {
        if (!isHub && rnd() < 0.7) edges.push({ source: `${k}_${i}`, target: `${k}_0`, color: g.c, opacity: 0.5 + rnd() * 0.35 });
        if (!isHub && rnd() < 0.18) {
          const t = Math.floor(rnd() * count);
          edges.push({ source: `${k}_${i}`, target: `${k}_${t}`, color: g.c, opacity: 0.35 + rnd() * 0.3 });
        }
      }
    }
  });

  return { nodes, edges };
}
