import { useMemo } from 'react';
import { buildHeroGraph } from './dataCrystal.data';

/** Static still-frame of the constellation (declarative; SSR + no-JS + reduced-motion). */
export default function DataCrystalFallback(): JSX.Element {
  const { nodes, edges } = useMemo(() => buildHeroGraph(), []);
  // Map layout coords (~[-300,300]x[-185,185]) into a 1000x600 viewBox, centered.
  const VW = 1000, VH = 600, cx = VW / 2, cy = VH / 2, k = 1.25;
  const px = (x: number) => cx + x * k;
  const py = (y: number) => cy + y * k;
  const id = new Map(nodes.map((n) => [n.id, n] as const));
  return (
    <svg className="hero-crystal-fallback" viewBox={`0 0 ${VW} ${VH}`}
         preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      <g stroke="#00e5ff" strokeOpacity="0.06" strokeWidth="0.6">
        {edges.map((e, i) => {
          const a = id.get(e.source), b = id.get(e.target);
          if (!a || !b) return null;
          return <line key={i} x1={px(a.x)} y1={py(a.y)} x2={px(b.x)} y2={py(b.y)} />;
        })}
      </g>
      {nodes.map((n) => (
        <circle key={n.id} cx={px(n.x)} cy={py(n.y)} r={n.radius * 0.6}
                fill={n.color} fillOpacity={n.opacity * 0.5} />
      ))}
    </svg>
  );
}
