import { useRef, useState, useCallback } from "react";
import { FeatureGallery, type Shot } from "./FeatureGallery";

export default function ScreenshotStrip({ shots }: { shots: Shot[] }): JSX.Element {
  const stripRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState<number | null>(null);
  const page = (dir: number) => stripRef.current?.scrollBy({ left: dir * 560, behavior: "smooth" });
  const close = useCallback(() => setOpen(null), []);

  return (
    <section className="showcase">
      <p className="showcase-label">See it in action</p>
      <div className="showcase-wrap">
        <button type="button" className="showcase-arrow showcase-arrow--l"
                aria-label="Scroll screenshots left" onClick={() => page(-1)}>&#8249;</button>
        <button type="button" className="showcase-arrow showcase-arrow--r"
                aria-label="Scroll screenshots right" onClick={() => page(1)}>&#8250;</button>
        <div className="showcase-fade showcase-fade--l" />
        <div className="showcase-fade showcase-fade--r" />
        <div className="showcase-strip" ref={stripRef}>
          {shots.map((s, i) => (
            <button type="button" key={s.title} className="showcase-shot"
                    aria-label={`Enlarge the ${s.title} screenshot`} onClick={() => setOpen(i)}>
              <span className="showcase-img" style={{ backgroundImage: `url('${s.image}')`, backgroundSize: s.zoom ?? '135%', backgroundPosition: s.focus ?? 'top center' }}>
                <span className="showcase-expand" aria-hidden="true">&#11138;</span>
              </span>
              <span className="showcase-cap">{s.title}</span>
            </button>
          ))}
        </div>
      </div>
      {open !== null && (
        <FeatureGallery shots={shots} index={open} onClose={close} onNavigate={setOpen} />
      )}
    </section>
  );
}
