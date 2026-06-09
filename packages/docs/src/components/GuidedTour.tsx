import { Fragment, useCallback, useMemo, useState } from "react";
import { FeatureGallery, type Shot } from "./FeatureGallery";

const SHOT = "/img/screenshots";

/* One "See it in action" section, organized into area-based flow groups. Every
   screenshot appears once; the provenance walkthrough is folded in as the final
   "Ask & trace" group (Answer → Connections → Evidence → Source). Per-shot
   focus/zoom crop each thumbnail onto its content rather than the page header. */
const GROUPS: { n: string; label: string; shots: Shot[] }[] = [
  {
    n: "1",
    label: "Set up",
    shots: [
      { title: "Connect your AI", image: `${SHOT}/setup-llm-provider.png`, full: `${SHOT}/setup-llm-provider.png`, caption: "Ollama, OpenAI, Anthropic, or Gemini.", alt: "First-run setup connecting an LLM provider", focus: "55% 74%", zoom: "150%" },
      { title: "Dashboard", image: `${SHOT}/app-dashboard.png`, full: `${SHOT}/app-dashboard.png`, caption: "Your knowledge base at a glance.", alt: "Dashboard showing 184 entities and 440 relationships over the graph", focus: "60% 30%", zoom: "112%" },
    ],
  },
  {
    n: "2",
    label: "Add your sources",
    shots: [
      { title: "Upload", image: `${SHOT}/sources-upload-dialog.png`, full: `${SHOT}/sources-upload-dialog.png`, caption: "Drop in PDFs, docs, web pages, audio, video.", alt: "Add Source dialog with a URL field and a file drop zone", focus: "50% 52%", zoom: "125%" },
      { title: "Source page", image: `${SHOT}/app-source-overview.png`, full: `${SHOT}/app-source-overview.png`, caption: "Chunked, extracted, and indexed automatically.", alt: "Source overview with entity, relationship and chunk stats", focus: "58% 30%", zoom: "140%" },
    ],
  },
  {
    n: "3",
    label: "Entities & relationships",
    shots: [
      { title: "Entities", image: `${SHOT}/app-source-entities.png`, full: `${SHOT}/app-source-entities.png`, caption: "Extracted automatically.", alt: "Extraction tab showing extracted entities with type badges", focus: "58% 64%", zoom: "150%" },
      { title: "Relationships", image: `${SHOT}/app-source-relationships.png`, full: `${SHOT}/app-source-relationships.png`, caption: "…and the links between them.", alt: "Extraction tab table of relationships with confidence scores", focus: "58% 66%", zoom: "145%" },
      { title: "Entity detail", image: `${SHOT}/app-entity-detail.png`, full: `${SHOT}/app-entity-detail.png`, caption: "Rich metadata on every entity.", alt: "Pierre Bezúkhov entity detail with 58 properties", focus: "58% 42%", zoom: "135%" },
      { title: "Templates", image: `${SHOT}/app-source-templates.png`, full: `${SHOT}/app-source-templates.png`, caption: "Typed by node & edge schemas.", alt: "Templates tab with node and edge type usage counts", focus: "58% 58%", zoom: "145%" },
    ],
  },
  {
    n: "4",
    label: "Explore the graph",
    shots: [
      { title: "Graph canvas", image: `${SHOT}/app-graph-default.png`, full: `${SHOT}/app-graph-default.png`, caption: "Explore the entire graph.", alt: "Force-directed knowledge graph of 184 entities", focus: "40% 60%", zoom: "175%" },
      { title: "Radial view", image: `${SHOT}/app-graph-mode.png`, full: `${SHOT}/app-graph-mode.png`, caption: "Multiple layouts — radial, force, and more.", alt: "Knowledge graph in a radial layout", focus: "50% 82%", zoom: "165%" },
    ],
  },
  {
    n: "5",
    label: "Ask & trace any answer",
    shots: [
      { title: "Answer", image: `${SHOT}/app-chat.png`, full: `${SHOT}/app-chat.png`, caption: "A cited answer — see what it's based on.", alt: "GraphRAG chat answer grounded in cited sources", focus: "58% 40%", zoom: "135%" },
      { title: "Connections", image: `${SHOT}/app-entity-connections.png`, full: `${SHOT}/app-entity-connections.png`, caption: "The entities and relationships it traversed.", alt: "Entity connections table listing related entities", focus: "58% 55%", zoom: "140%" },
      { title: "Evidence", image: `${SHOT}/app-relationship-detail.png`, full: `${SHOT}/app-relationship-detail.png`, caption: "Each link scored, with the model's justification.", alt: "Relationship detail with a 90% confidence score and justification", focus: "58% 45%", zoom: "135%" },
      { title: "Source", image: `${SHOT}/app-source-chunks.png`, full: `${SHOT}/app-source-chunks.png`, caption: "Down to the exact chunk in your document.", alt: "Source chunks grid showing per-chunk extracted entity counts", focus: "58% 60%", zoom: "145%" },
    ],
  },
  {
    n: "6",
    label: "Or drive it from the terminal",
    shots: [
      { title: "Explore the commands", image: `${SHOT}/cli-overview.png`, full: `${SHOT}/cli-overview.png`, caption: "One CLI for setup, sources, graph, and chat.", alt: "chaoscypher --help showing the CLI commands", focus: "left top", zoom: "cover" },
      { title: "Manage sources", image: `${SHOT}/cli-sources.png`, full: `${SHOT}/cli-sources.png`, caption: "Add, list, and track documents from the shell.", alt: "chaoscypher source list showing an ingested file", focus: "left top", zoom: "cover" },
      { title: "Search your graph", image: `${SHOT}/cli-search.png`, full: `${SHOT}/cli-search.png`, caption: "Hybrid GraphRAG search, scored — in the terminal.", alt: "chaoscypher source search results for Napoleon", focus: "left top", zoom: "cover" },
    ],
  },
];

export default function GuidedTour(): JSX.Element {
  const [open, setOpen] = useState<number | null>(null);
  const close = useCallback(() => setOpen(null), []);
  const allShots = useMemo(() => GROUPS.flatMap((g) => g.shots), []);
  // flat index where each group starts, so the shared lightbox can browse all shots
  const offsets = useMemo(() => {
    let acc = 0;
    return GROUPS.map((g) => {
      const start = acc;
      acc += g.shots.length;
      return start;
    });
  }, []);

  return (
    <section className="tour">
      <p className="showcase-label">See it in action</p>
      {GROUPS.map((g, gi) => (
        <div className="tour-group" key={g.label}>
          <p className="tour-group-label">
            <span className="tour-group-num">{g.n}</span>
            {g.label}
          </p>
          <div className="tour-flow">
            {g.shots.map((s, i) => (
              <Fragment key={s.title}>
                <button
                  type="button"
                  className="tour-step"
                  aria-label={`${g.label}: ${s.title} — enlarge screenshot`}
                  onClick={() => setOpen(offsets[gi] + i)}
                >
                  <span
                    className="tour-thumb"
                    style={{
                      backgroundImage: `url('${s.image}')`,
                      backgroundSize: s.zoom ?? "115%",
                      backgroundPosition: s.focus ?? "top center",
                    }}
                  >
                    <span className="showcase-expand" aria-hidden="true">&#11138;</span>
                  </span>
                  <span className="tour-step-label">{s.title}</span>
                  <span className="tour-cap">{s.caption}</span>
                </button>
                {i < g.shots.length - 1 && (
                  <span className="tour-arrow" aria-hidden="true">&#8594;</span>
                )}
              </Fragment>
            ))}
          </div>
        </div>
      ))}
      {open !== null && (
        <FeatureGallery shots={allShots} index={open} onClose={close} onNavigate={setOpen} />
      )}
    </section>
  );
}
