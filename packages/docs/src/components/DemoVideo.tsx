import useBaseUrl from "@docusaurus/useBaseUrl";

/* The ~60s product demo (capture + assembly pipeline in
   internal/scripts/demo/), rendered as the lead of the "See it in action"
   section (see GuidedTour). Click-to-play with a poster — not an autoplay
   ambient loop like GraphHero — because it has captions and runs nearly a
   minute. The screenshot walkthrough flows directly below it. */
export default function DemoVideo(): JSX.Element {
  const video = useBaseUrl("/video/demo.webm");
  const poster = useBaseUrl("/video/demo-poster.png");

  return (
    <div className="demo-video">
      <div className="demo-video-frame">
        <video
          controls
          preload="none"
          poster={poster}
          playsInline
          aria-label="Chaos Cypher product tour: upload, extraction, graph, cited chat, and source tracing"
        >
          <source src={video} type="video/webm" />
        </video>
      </div>
      <p className="demo-video-cap">Watch the full tour, or step through each part below.</p>
    </div>
  );
}
