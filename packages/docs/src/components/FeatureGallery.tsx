import { useEffect, useRef, useCallback } from "react";

export interface Shot {
  title: string;
  image: string;   // thumbnail (card jpg)
  full: string;    // full-res png for the lightbox
  caption: string;
  alt: string;
  focus?: string;  // CSS background-position for the thumbnail crop
  zoom?: string;   // CSS background-size for the thumbnail (e.g. '135%')
}

export function FeatureGallery({
  shots,
  index,
  onClose,
  onNavigate,
}: {
  shots: Shot[];
  index: number;
  onClose: () => void;
  onNavigate: (n: number) => void;
}): JSX.Element {
  const closeRef = useRef<HTMLButtonElement>(null);
  const feature = shots[index];
  const go = useCallback(
    (delta: number) => onNavigate((index + delta + shots.length) % shots.length),
    [index, onNavigate, shots.length],
  );

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowRight") go(1);
      else if (e.key === "ArrowLeft") go(-1);
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [go, onClose]);

  return (
    <div
      className="fg-overlay"
      role="dialog"
      aria-modal="true"
      aria-label={`${feature.title} — enlarged screenshot`}
      onClick={onClose}
    >
      <button
        ref={closeRef}
        type="button"
        className="fg-close"
        aria-label="Close"
        onClick={onClose}
      >
        &#10005;
      </button>
      <button
        type="button"
        className="fg-arrow fg-prev"
        aria-label="Previous feature"
        onClick={(e) => {
          e.stopPropagation();
          go(-1);
        }}
      >
        &#8249;
      </button>
      <button
        type="button"
        className="fg-arrow fg-next"
        aria-label="Next feature"
        onClick={(e) => {
          e.stopPropagation();
          go(1);
        }}
      >
        &#8250;
      </button>
      <div className="fg-stage" onClick={(e) => e.stopPropagation()}>
        <div className="fg-frame">
          <img className="fg-img" src={feature.full} alt={feature.alt} />
        </div>
        <div className="fg-cap">
          <h3>{feature.title}</h3>
          <p>{feature.caption}</p>
        </div>
        <div className="fg-dots">
          {shots.map((s, i) => (
            <button
              key={s.title}
              type="button"
              className={`fg-dot${i === index ? " on" : ""}`}
              aria-label={`Show ${s.title}`}
              aria-current={i === index}
              onClick={() => onNavigate(i)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
