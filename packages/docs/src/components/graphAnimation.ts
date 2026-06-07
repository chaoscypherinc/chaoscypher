/**
 * Canvas-rendered flying knowledge graph animation.
 *
 * Reads all colors and parameters from CSS custom properties (--cc-*)
 * so the animation is fully theme-driven with zero hardcoded values.
 */

// ---- Types ----

interface RGB {
  r: number;
  g: number;
  b: number;
}

interface NodeTemplate {
  x: number;
  y: number;
  color: string;
  label: string;
  r: number;
}

interface ClusterTemplate {
  nodes: NodeTemplate[];
  edges: [number, number, string][];
}

interface NodeState {
  ox: number;
  oy: number;
  vx: number;
  vy: number;
  breathPhase: number;
  breathSpeed: number;
  breathAmp: number;
  orbitPhase: number;
  orbitSpeed: number;
  orbitRadius: number;
}

interface EdgeState {
  pulsePhase: number;
  pulseSpeed: number;
  flowOffset: number;
  flowSpeed: number;
}

interface Cluster {
  tmpl: ClusterTemplate;
  angle: number;
  speed: number;
  size: number;
  startDist: number;
  endDist: number;
  progress: number;
  nodeStates: NodeState[];
  edgeStates: EdgeState[];
  layer: number;
  maxAlpha: number;
  rotation: number;
  currentRotation: number;
  _screenPos?: { x: number; y: number };
  _screenAlpha?: number;
}

interface Particle {
  angle: number;
  speed: number;
  startDist: number;
  progress: number;
  r: number;
  color: RGB;
  wobblePhase: number;
  wobbleSpeed: number;
  wobbleAmp: number;
  maxAlpha: number;
}

interface Nebula {
  x: number;
  y: number;
  vx: number;
  vy: number;
  radius: number;
  color: RGB;
  alpha: number;
  pulsePhase: number;
  pulseSpeed: number;
}

// ---- Module state ----

let animFrameId: number | null = null;
let canvas: HTMLCanvasElement | null = null;
let ctx: CanvasRenderingContext2D | null = null;
let W = 0;
let H = 0;
let cx = 0;
let cy = 0;
let lastTime = 0;

let colors: Record<string, RGB> = {};
let colorKeys: string[] = [];
let clusters: Cluster[] = [];
let particles: Particle[] = [];
let nebulae: Nebula[] = [];

// Params from CSS
let CLUSTER_COUNT = 18;
let PARTICLE_COUNT = 120;
let NEBULA_COUNT = 6;
let CLUSTER_SPEED = 0.07;
let CLUSTER_SIZE_MIN = 35;
let CLUSTER_SIZE_MAX = 85;
let LAYER_FAR_PCT = 0.45;
let LAYER_MID_PCT = 0.35;
let FAR_MAX_ALPHA = 0.35;
let MID_MAX_ALPHA = 0.55;
let NEAR_MAX_ALPHA = 0.8;

// ---- Cluster templates ----

const TEMPLATES: ClusterTemplate[] = [
  {
    nodes: [
      { x: -0.6, y: -0.5, color: "magenta", label: "Person", r: 5 },
      { x: 0.6, y: -0.4, color: "purple", label: "Company", r: 6 },
      { x: 0.0, y: 0.6, color: "cyan", label: "Location", r: 4 },
    ],
    edges: [
      [0, 1, "worksAt"],
      [1, 2, "locatedIn"],
      [2, 0, "livesIn"],
    ],
  },
  {
    nodes: [
      { x: 0.0, y: 0.0, color: "magenta", label: "Topic", r: 7 },
      { x: -0.7, y: -0.6, color: "cyan", label: "Doc.pdf", r: 4 },
      { x: 0.7, y: -0.5, color: "purple", label: "Paper", r: 5 },
      { x: 0.6, y: 0.6, color: "cyan", label: "Source", r: 4 },
      { x: -0.6, y: 0.5, color: "green", label: "Notes", r: 3 },
    ],
    edges: [
      [0, 1, "mentions"],
      [0, 2, "cites"],
      [0, 3, "from"],
      [0, 4, "links"],
    ],
  },
  {
    nodes: [
      { x: -0.8, y: 0.0, color: "cyan", label: "Chunk", r: 5 },
      { x: -0.27, y: 0.0, color: "purple", label: "Entity", r: 5 },
      { x: 0.27, y: 0.0, color: "magenta", label: "Relation", r: 5 },
      { x: 0.8, y: 0.0, color: "green", label: "Graph", r: 5 },
    ],
    edges: [
      [0, 1, "extract"],
      [1, 2, "resolve"],
      [2, 3, "commit"],
    ],
  },
  {
    nodes: [
      { x: 0.0, y: -0.7, color: "magenta", label: "Research", r: 6 },
      { x: 0.7, y: 0.0, color: "cyan", label: "Paper", r: 4 },
      { x: 0.0, y: 0.7, color: "purple", label: "Theory", r: 5 },
      { x: -0.7, y: 0.0, color: "green", label: "Evidence", r: 4 },
    ],
    edges: [
      [0, 1, "cites"],
      [1, 2, "supports"],
      [2, 3, "proves"],
      [3, 0, "validates"],
    ],
  },
  {
    nodes: [
      { x: -0.5, y: 0.0, color: "magenta", label: "Author", r: 5 },
      { x: 0.5, y: 0.0, color: "cyan", label: "Publication", r: 5 },
    ],
    edges: [[0, 1, "authored"]],
  },
  {
    nodes: [
      { x: 0.0, y: 0.0, color: "magenta", label: "Template", r: 8 },
      { x: -0.7, y: -0.6, color: "purple", label: "Schema", r: 4 },
      { x: 0.7, y: -0.5, color: "cyan", label: "Domain", r: 5 },
      { x: 0.7, y: 0.5, color: "green", label: "Instance", r: 4 },
      { x: -0.7, y: 0.6, color: "cyan", label: "Property", r: 4 },
      { x: 0.0, y: -0.8, color: "orange", label: "Type", r: 3 },
    ],
    edges: [
      [0, 1, "hasSchema"],
      [0, 2, "inDomain"],
      [0, 3, "creates"],
      [0, 4, "defines"],
      [0, 5, "isType"],
      [1, 2, ""],
      [3, 4, ""],
    ],
  },
  {
    nodes: [
      { x: 0.0, y: 0.6, color: "cyan", label: "Search", r: 6 },
      { x: -0.6, y: -0.4, color: "magenta", label: "Hit #1", r: 4 },
      { x: 0.0, y: -0.5, color: "purple", label: "Hit #2", r: 3 },
      { x: 0.6, y: -0.4, color: "magenta", label: "Hit #3", r: 4 },
    ],
    edges: [
      [0, 1, "0.94"],
      [0, 2, "0.87"],
      [0, 3, "0.81"],
    ],
  },
  {
    nodes: [
      { x: 0.0, y: -0.5, color: "green", label: "Concept", r: 4 },
      { x: -0.5, y: 0.4, color: "magenta", label: "Ref A", r: 3 },
      { x: 0.5, y: 0.4, color: "purple", label: "Ref B", r: 3 },
    ],
    edges: [
      [0, 1, "relates"],
      [0, 2, "relates"],
      [1, 2, ""],
    ],
  },
  {
    nodes: [
      { x: -0.4, y: -0.3, color: "orange", label: "Event", r: 5 },
      { x: 0.4, y: -0.3, color: "cyan", label: "Date", r: 4 },
      { x: 0.0, y: 0.5, color: "purple", label: "Actor", r: 5 },
    ],
    edges: [
      [0, 1, "on"],
      [0, 2, "by"],
      [1, 2, ""],
    ],
  },
  {
    nodes: [
      { x: 0.0, y: 0.0, color: "cyan", label: "Query", r: 6 },
      { x: -0.8, y: -0.3, color: "magenta", label: "Vector", r: 3 },
      { x: 0.8, y: -0.3, color: "purple", label: "Graph", r: 3 },
      { x: -0.5, y: 0.6, color: "green", label: "Keyword", r: 3 },
      { x: 0.5, y: 0.6, color: "orange", label: "Hybrid", r: 3 },
    ],
    edges: [
      [0, 1, ""],
      [0, 2, ""],
      [0, 3, ""],
      [0, 4, ""],
      [1, 3, ""],
      [2, 4, ""],
    ],
  },
];

// ---- Helpers ----

function rgba(c: RGB, a: number): string {
  return `rgba(${c.r},${c.g},${c.b},${a})`;
}

function rand(min: number, max: number): number {
  return min + Math.random() * (max - min);
}

function parseRGB(hex: string): RGB {
  const h = hex.trim().replace("#", "");
  const r = parseInt(h.substring(0, 2), 16);
  const g = parseInt(h.substring(2, 4), 16);
  const b = parseInt(h.substring(4, 6), 16);
  return {
    r: isNaN(r) ? 0 : r,
    g: isNaN(g) ? 0 : g,
    b: isNaN(b) ? 0 : b,
  };
}

function readCSSVar(name: string, fallback: string): string {
  const val = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  return val || fallback;
}

function readCSSNum(name: string, fallback: number): number {
  const val = parseFloat(readCSSVar(name, String(fallback)));
  return isNaN(val) ? fallback : val;
}

// ---- Initialization ----

function readTheme(): void {
  colors = {
    magenta: parseRGB(readCSSVar("--cc-accent-magenta", "#ff2d95")),
    purple: parseRGB(readCSSVar("--cc-accent-purple", "#7b2ff7")),
    cyan: parseRGB(readCSSVar("--cc-accent-cyan", "#00fff0")),
    green: parseRGB(readCSSVar("--cc-accent-green", "#39ff14")),
    orange: parseRGB(readCSSVar("--cc-accent-orange", "#ff6d00")),
  };
  colorKeys = Object.keys(colors);

  CLUSTER_COUNT = readCSSNum("--cc-hero-cluster-count", 18);
  PARTICLE_COUNT = readCSSNum("--cc-hero-particle-count", 120);
  NEBULA_COUNT = readCSSNum("--cc-hero-nebula-count", 6);
  CLUSTER_SPEED = readCSSNum("--cc-hero-cluster-speed", 0.07);
  CLUSTER_SIZE_MIN = readCSSNum("--cc-hero-cluster-size-min", 35);
  CLUSTER_SIZE_MAX = readCSSNum("--cc-hero-cluster-size-max", 85);
  LAYER_FAR_PCT = readCSSNum("--cc-hero-layer-far-pct", 0.45);
  LAYER_MID_PCT = readCSSNum("--cc-hero-layer-mid-pct", 0.35);
  FAR_MAX_ALPHA = readCSSNum("--cc-hero-far-max-alpha", 0.35);
  MID_MAX_ALPHA = readCSSNum("--cc-hero-mid-max-alpha", 0.55);
  NEAR_MAX_ALPHA = readCSSNum("--cc-hero-near-max-alpha", 0.8);
}

function resize(): void {
  if (!canvas) return;
  // Try multiple sources for Firefox compatibility
  const parent = canvas.parentElement;
  const rect = canvas.getBoundingClientRect();
  const parentRect = parent?.getBoundingClientRect();
  const w = Math.round(rect.width)
    || Math.round(parentRect?.width ?? 0)
    || window.innerWidth;
  const h = Math.round(rect.height)
    || Math.round(parentRect?.height ?? 0)
    || window.innerHeight;
  if (w === 0 || h === 0) return;
  W = canvas.width = w;
  H = canvas.height = h;
  cx = W / 2;
  cy = H / 2;
}

// ---- Spawners ----

function spawnCluster(staggerProgress?: number): void {
  const tmpl = TEMPLATES[Math.floor(Math.random() * TEMPLATES.length)];
  const angle = Math.random() * Math.PI * 2;
  const speed = rand(0.1, 0.28);
  const size = rand(CLUSTER_SIZE_MIN, CLUSTER_SIZE_MAX);

  const layerRoll = Math.random();
  let layer: number, maxAlpha: number;
  if (layerRoll < LAYER_FAR_PCT) {
    layer = 0;
    maxAlpha = rand(FAR_MAX_ALPHA * 0.6, FAR_MAX_ALPHA);
  } else if (layerRoll < LAYER_FAR_PCT + LAYER_MID_PCT) {
    layer = 1;
    maxAlpha = rand(MID_MAX_ALPHA * 0.7, MID_MAX_ALPHA);
  } else {
    layer = 2;
    maxAlpha = rand(NEAR_MAX_ALPHA * 0.6, NEAR_MAX_ALPHA);
  }

  const nodeStates: NodeState[] = tmpl.nodes.map(() => ({
    ox: 0,
    oy: 0,
    vx: rand(-0.3, 0.3),
    vy: rand(-0.3, 0.3),
    breathPhase: Math.random() * Math.PI * 2,
    breathSpeed: rand(1.2, 3.2),
    breathAmp: rand(0.06, 0.18),
    orbitPhase: Math.random() * Math.PI * 2,
    orbitSpeed: rand(0.2, 0.8),
    orbitRadius: rand(0.04, 0.15),
  }));

  const edgeStates: EdgeState[] = tmpl.edges.map(() => ({
    pulsePhase: Math.random() * Math.PI * 2,
    pulseSpeed: rand(0.8, 3.3),
    flowOffset: Math.random(),
    flowSpeed: rand(0.2, 0.7),
  }));

  clusters.push({
    tmpl,
    angle,
    speed,
    size,
    startDist: rand(0.01, 0.06),
    endDist: rand(1.2, 2.2),
    progress: staggerProgress ?? 0,
    nodeStates,
    edgeStates,
    layer,
    maxAlpha,
    rotation: rand(-0.2, 0.2),
    currentRotation: rand(-Math.PI, Math.PI),
  });
}

function spawnParticle(stagger: boolean): Particle {
  const color = colors[colorKeys[Math.floor(Math.random() * colorKeys.length)]];
  return {
    angle: Math.random() * Math.PI * 2,
    speed: rand(0.01, 0.05),
    startDist: rand(0.01, 0.08),
    progress: stagger ? Math.random() : 0,
    r: rand(0.5, 2),
    color,
    wobblePhase: Math.random() * Math.PI * 2,
    wobbleSpeed: rand(0.5, 2.5),
    wobbleAmp: rand(5, 20),
    maxAlpha: rand(0.15, 0.45),
  };
}

function initState(): void {
  clusters = [];
  particles = [];
  nebulae = [];

  for (let i = 0; i < CLUSTER_COUNT; i++) {
    spawnCluster((i / CLUSTER_COUNT) * 0.88);
  }

  for (let i = 0; i < PARTICLE_COUNT; i++) {
    particles.push(spawnParticle(true));
  }

  for (let i = 0; i < NEBULA_COUNT; i++) {
    nebulae.push({
      x: rand(-0.2, 1.2),
      y: rand(-0.2, 1.2),
      vx: rand(-0.01, 0.01),
      vy: rand(-0.01, 0.01),
      radius: rand(100, 300),
      color: colors[colorKeys[Math.floor(Math.random() * colorKeys.length)]],
      alpha: rand(0.02, 0.05),
      pulsePhase: Math.random() * Math.PI * 2,
      pulseSpeed: rand(0.3, 0.8),
    });
  }
}

// ---- Render loop ----

function animate(now: number): void {
  const dt = Math.min((now - lastTime) / 1000, 0.05);
  lastTime = now;
  if (!ctx) return;
  ctx.clearRect(0, 0, W, H);

  // Nebulae
  for (const neb of nebulae) {
    neb.x += neb.vx * dt;
    neb.y += neb.vy * dt;
    if (neb.x < -0.3) neb.x = 1.3;
    if (neb.x > 1.3) neb.x = -0.3;
    if (neb.y < -0.3) neb.y = 1.3;
    if (neb.y > 1.3) neb.y = -0.3;
    neb.pulsePhase += neb.pulseSpeed * dt;
    const a = neb.alpha * (0.7 + 0.3 * Math.sin(neb.pulsePhase));
    const px = neb.x * W;
    const py = neb.y * H;
    const grad = ctx.createRadialGradient(px, py, 0, px, py, neb.radius);
    grad.addColorStop(0, rgba(neb.color, a));
    grad.addColorStop(1, rgba(neb.color, 0));
    ctx.beginPath();
    ctx.arc(px, py, neb.radius, 0, Math.PI * 2);
    ctx.fillStyle = grad;
    ctx.fill();
  }

  // Replenish clusters
  while (clusters.length < CLUSTER_COUNT) spawnCluster();

  // Sort: far first
  clusters.sort((a, b) => a.layer - b.layer);

  // Update + draw clusters
  for (let ci = clusters.length - 1; ci >= 0; ci--) {
    const c = clusters[ci];
    c.progress += dt * c.speed * CLUSTER_SPEED;
    c.currentRotation += c.rotation * dt;

    if (c.progress >= 1) {
      clusters.splice(ci, 1);
      continue;
    }

    const t = c.progress;
    const easedT = t * t;
    const dist = c.startDist + (c.endDist - c.startDist) * easedT;
    const scale = 0.08 + easedT * 2.5;
    const clX = cx + Math.cos(c.angle) * dist * Math.min(W, H) * 0.5;
    const clY = cy + Math.sin(c.angle) * dist * Math.min(H, W) * 0.5;
    const clSize = c.size * scale;

    let alpha: number;
    if (t < 0.08) alpha = t / 0.08;
    else if (t > 0.82) alpha = (1 - t) / 0.18;
    else alpha = 1;
    alpha *= c.maxAlpha;

    c._screenPos = { x: clX, y: clY };
    c._screenAlpha = alpha;

    const isFar = c.layer === 0;
    const isMid = c.layer === 1;

    // Node positions
    const nodePos: { px: number; py: number }[] = [];
    for (let ni = 0; ni < c.tmpl.nodes.length; ni++) {
      const n = c.tmpl.nodes[ni];
      const ns = c.nodeStates[ni];
      ns.orbitPhase += ns.orbitSpeed * dt;
      ns.breathPhase += ns.breathSpeed * dt;
      const breathScale = 1 + Math.sin(ns.breathPhase) * ns.breathAmp;
      ns.vx += rand(-1.25, 1.25) * dt;
      ns.vy += rand(-1.25, 1.25) * dt;
      ns.vx *= 0.96;
      ns.vy *= 0.96;
      ns.ox += ns.vx * dt;
      ns.oy += ns.vy * dt;
      ns.ox *= 0.96;
      ns.oy *= 0.96;
      const rx =
        (n.x + Math.cos(ns.orbitPhase) * ns.orbitRadius + ns.ox) * breathScale;
      const ry =
        (n.y + Math.sin(ns.orbitPhase) * ns.orbitRadius + ns.oy) * breathScale;
      const cos = Math.cos(c.currentRotation);
      const sin = Math.sin(c.currentRotation);
      nodePos.push({
        px: clX + (rx * cos - ry * sin) * clSize,
        py: clY + (rx * sin + ry * cos) * clSize,
      });
    }

    // Edges
    for (let ei = 0; ei < c.tmpl.edges.length; ei++) {
      const [fi, ti, label] = c.tmpl.edges[ei];
      const es = c.edgeStates[ei];
      const from = nodePos[fi];
      const to = nodePos[ti];
      const fc = colors[c.tmpl.nodes[fi].color];
      const tc = colors[c.tmpl.nodes[ti].color];

      es.pulsePhase += es.pulseSpeed * dt;
      const ea = alpha * (0.2 + 0.15 * Math.sin(es.pulsePhase));

      const grad = ctx.createLinearGradient(from.px, from.py, to.px, to.py);
      grad.addColorStop(0, rgba(fc, ea));
      grad.addColorStop(1, rgba(tc, ea));
      ctx.beginPath();
      ctx.moveTo(from.px, from.py);
      ctx.lineTo(to.px, to.py);
      ctx.strokeStyle = grad;
      ctx.lineWidth = isFar ? 0.4 : isMid ? 0.6 : Math.max(0.5, scale * 0.5);
      ctx.stroke();

      // Flow dot
      es.flowOffset = (es.flowOffset + es.flowSpeed * dt) % 1;
      const dT = es.flowOffset;
      const dX = from.px + (to.px - from.px) * dT;
      const dY = from.py + (to.py - from.py) * dT;
      const dR = isFar ? 0.6 : isMid ? 1 : Math.max(0.8, scale * 0.7);
      const mc: RGB = {
        r: (fc.r + tc.r) / 2,
        g: (fc.g + tc.g) / 2,
        b: (fc.b + tc.b) / 2,
      };
      ctx.beginPath();
      ctx.arc(dX, dY, dR, 0, Math.PI * 2);
      ctx.fillStyle = rgba(mc, alpha * 0.7);
      ctx.fill();

      // Label
      if (label && !isFar && scale > 0.4 && alpha > 0.15) {
        const fs = Math.max(6, Math.min(10, scale * 4));
        ctx.font = `${fs}px "Courier New", monospace`;
        ctx.fillStyle = rgba(mc, alpha * 0.3);
        ctx.textAlign = "center";
        ctx.fillText(label, (from.px + to.px) / 2, (from.py + to.py) / 2 - fs * 0.4);
      }
    }

    // Nodes
    for (let ni = 0; ni < c.tmpl.nodes.length; ni++) {
      const n = c.tmpl.nodes[ni];
      const pos = nodePos[ni];
      const col = colors[n.color];
      const baseR = n.r * scale * 0.45;
      const r = isFar ? baseR * 0.7 : isMid ? baseR * 0.85 : baseR;
      if (r < 0.3) continue;

      // Glow
      const glowR = r * (isFar ? 5 : isMid ? 4 : 3.5);
      const gGrad = ctx.createRadialGradient(pos.px, pos.py, r * 0.2, pos.px, pos.py, glowR);
      gGrad.addColorStop(0, rgba(col, alpha * (isFar ? 0.15 : 0.2)));
      gGrad.addColorStop(1, rgba(col, 0));
      ctx.beginPath();
      ctx.arc(pos.px, pos.py, glowR, 0, Math.PI * 2);
      ctx.fillStyle = gGrad;
      ctx.fill();

      // Body
      ctx.beginPath();
      ctx.arc(pos.px, pos.py, r, 0, Math.PI * 2);
      ctx.fillStyle = rgba(col, alpha * (isFar ? 0.5 : 0.85));
      ctx.fill();

      // Center highlight
      if (!isFar && r > 1) {
        ctx.beginPath();
        ctx.arc(pos.px, pos.py, r * 0.35, 0, Math.PI * 2);
        ctx.fillStyle = rgba({ r: 255, g: 255, b: 255 }, alpha * 0.4);
        ctx.fill();
      }

      // Label
      if (c.layer === 2 && scale > 0.5 && alpha > 0.2 && n.label) {
        const fs = Math.max(7, Math.min(11, scale * 4.5));
        ctx.font = `${fs}px "Courier New", monospace`;
        ctx.fillStyle = rgba(col, alpha * 0.5);
        ctx.textAlign = "center";
        ctx.fillText(n.label, pos.px, pos.py - r - fs * 0.5);
      }
    }
  }

  // Inter-cluster threads
  for (let i = 0; i < clusters.length; i++) {
    for (let j = i + 1; j < clusters.length; j++) {
      const ci = clusters[i];
      const cj = clusters[j];
      if (!ci._screenPos || !cj._screenPos) continue;
      const dx = ci._screenPos.x - cj._screenPos.x;
      const dy = ci._screenPos.y - cj._screenPos.y;
      const d = Math.sqrt(dx * dx + dy * dy);
      if (d < 250 && d > 30) {
        const a = (1 - d / 250) * 0.08 * Math.min(ci._screenAlpha!, cj._screenAlpha!);
        if (a < 0.005) continue;
        ctx.beginPath();
        ctx.moveTo(ci._screenPos.x, ci._screenPos.y);
        ctx.lineTo(cj._screenPos.x, cj._screenPos.y);
        ctx.strokeStyle = `rgba(100,100,180,${a})`;
        ctx.lineWidth = 0.5;
        ctx.stroke();
      }
    }
  }

  // Particles
  for (let i = 0; i < particles.length; i++) {
    const p = particles[i];
    p.progress += p.speed * dt * 0.08;
    if (p.progress >= 1) {
      particles[i] = spawnParticle(false);
      continue;
    }
    const pt = p.progress;
    const pE = pt * pt;
    const pDist = p.startDist + 1.5 * pE;
    p.wobblePhase += p.wobbleSpeed * dt;
    const wb = Math.sin(p.wobblePhase) * p.wobbleAmp;
    const px = cx + Math.cos(p.angle) * pDist * Math.min(W, H) * 0.5 + wb;
    const py = cy + Math.sin(p.angle) * pDist * Math.min(H, W) * 0.5 + wb * 0.5;

    let pAlpha: number;
    if (pt < 0.1) pAlpha = pt / 0.1;
    else if (pt > 0.85) pAlpha = (1 - pt) / 0.15;
    else pAlpha = 1;
    pAlpha *= p.maxAlpha;

    const gR = p.r * 4;
    const pGrad = ctx.createRadialGradient(px, py, 0, px, py, gR);
    pGrad.addColorStop(0, rgba(p.color, pAlpha * 0.5));
    pGrad.addColorStop(1, rgba(p.color, 0));
    ctx.beginPath();
    ctx.arc(px, py, gR, 0, Math.PI * 2);
    ctx.fillStyle = pGrad;
    ctx.fill();

    ctx.beginPath();
    ctx.arc(px, py, p.r * (0.5 + pE), 0, Math.PI * 2);
    ctx.fillStyle = rgba(p.color, pAlpha);
    ctx.fill();
  }

  animFrameId = requestAnimationFrame(animate);
}

// ---- Public API ----

export function startAnimation(canvasEl: HTMLCanvasElement): void {
  canvas = canvasEl;
  ctx = canvas.getContext("2d");
  if (!ctx) return;

  readTheme();
  resize();

  // If canvas has zero dimensions (Firefox timing), retry after layout settles
  if (W === 0 || H === 0) {
    setTimeout(() => {
      resize();
      if (W === 0 || H === 0) {
        // Final fallback: force to window size
        W = canvas!.width = window.innerWidth;
        H = canvas!.height = window.innerHeight;
        cx = W / 2;
        cy = H / 2;
      }
      initState();
      lastTime = performance.now();
      animFrameId = requestAnimationFrame(animate);
    }, 100);
    window.addEventListener("resize", resize);
    return;
  }

  initState();
  lastTime = performance.now();

  window.addEventListener("resize", resize);
  animFrameId = requestAnimationFrame(animate);
}

export function stopAnimation(): void {
  if (animFrameId !== null) {
    cancelAnimationFrame(animFrameId);
    animFrameId = null;
  }
  window.removeEventListener("resize", resize);
  canvas = null;
  ctx = null;
  clusters = [];
  particles = [];
  nebulae = [];
}
