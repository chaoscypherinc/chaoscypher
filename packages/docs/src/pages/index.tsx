import { Fragment } from "react";
import Layout from "@theme/Layout";
import Link from "@docusaurus/Link";
import useDocusaurusContext from "@docusaurus/useDocusaurusContext";
import CodeBlock from "@theme/CodeBlock";
import GraphHero from "../components/GraphHero";
import ScreenshotStrip from "../components/ScreenshotStrip";
import type { Shot } from "../components/FeatureGallery";

function ArrowRight() {
  return <span aria-hidden="true"> &#8594;</span>;
}

/* ------------------------------------------------------------------ */
/*  Showcase shots                                                      */
/* ------------------------------------------------------------------ */

const SHOT = "/img/screenshots";
const SHOWCASE: Shot[] = [
  { title: 'First-time setup', image: `${SHOT}/setup-llm-provider.png`, full: `${SHOT}/setup-llm-provider.png`, caption: 'Connect your AI — Ollama, OpenAI, Anthropic, or Gemini', alt: 'First-run setup connecting an LLM provider' },
  { title: 'Dashboard', image: `${SHOT}/app-dashboard.png`, full: `${SHOT}/app-dashboard.png`, caption: 'Your knowledge base at a glance', alt: 'Dashboard showing 184 entities and 440 relationships over the graph' },
  { title: 'Knowledge graph', image: `${SHOT}/app-graph-default.png`, full: `${SHOT}/app-graph-default.png`, caption: 'Explore the entire graph', alt: 'Force-directed knowledge graph of 184 entities', focus: 'center', zoom: '120%' },
  { title: 'Radial view', image: `${SHOT}/app-graph-mode.png`, full: `${SHOT}/app-graph-mode.png`, caption: 'Multiple layouts — radial, force, and more', alt: 'Knowledge graph in a radial layout', focus: 'center', zoom: '115%' },
  { title: 'Processing', image: `${SHOT}/app-source-overview.png`, full: `${SHOT}/app-source-overview.png`, caption: 'Chunked, extracted, and indexed automatically', alt: 'Source overview: 215 entities, 462 relationships, 419 chunks' },
  { title: 'Chunks', image: `${SHOT}/app-source-chunks.png`, full: `${SHOT}/app-source-chunks.png`, caption: 'Every chunk, tracked and inspectable', alt: 'Chunks tab grid showing per-chunk entity counts' },
  { title: 'Extracted entities', image: `${SHOT}/app-source-entities.png`, full: `${SHOT}/app-source-entities.png`, caption: 'Entities extracted automatically', alt: 'Extraction tab showing extracted entities with type badges' },
  { title: 'Extracted relationships', image: `${SHOT}/app-source-relationships.png`, full: `${SHOT}/app-source-relationships.png`, caption: '…and the relationships between them', alt: 'Extraction tab table of relationships with confidence scores' },
  { title: 'Templates', image: `${SHOT}/app-source-templates.png`, full: `${SHOT}/app-source-templates.png`, caption: 'Typed by templates — node & edge schemas', alt: 'Templates tab with node and edge type usage counts' },
  { title: 'Entity detail', image: `${SHOT}/app-entity-detail.png`, full: `${SHOT}/app-entity-detail.png`, caption: 'Rich metadata on every entity', alt: 'Pierre Bezúkhov entity detail with 58 properties' },
  { title: 'Connections', image: `${SHOT}/app-entity-connections.png`, full: `${SHOT}/app-entity-connections.png`, caption: 'See everything an entity connects to', alt: 'Pierre Bezúkhov connections table with 14 relationships' },
  { title: 'Relationship detail', image: `${SHOT}/app-relationship-detail.png`, full: `${SHOT}/app-relationship-detail.png`, caption: 'Every relationship — justified and scored', alt: 'Relationship detail with a 90% confidence score and LLM justification' },
  { title: 'GraphRAG chat', image: `${SHOT}/app-chat.png`, full: `${SHOT}/app-chat.png`, caption: 'Ask graph-native questions vector search can\'t answer', alt: 'GraphRAG chat ranking entities by PageRank centrality' },
];

/* ------------------------------------------------------------------ */
/*  Section: See · Trust · Own value band                             */
/* ------------------------------------------------------------------ */

function ValueBand() {
  return (
    <section className="value-band">
      <h2>Knowledge you can see, trust, and own</h2>
      <div className="feature-grid">
        <div className="feature-card">
          <h3>See</h3>
          <p>
            An inspectable knowledge graph, not a black box. Explore every
            entity, relationship, and source behind an answer.
          </p>
        </div>
        <div className="feature-card">
          <h3>Trust</h3>
          <p>
            Backed by your sources, not guesswork. Every answer shows the
            documents, chunks, and connections it came from.
          </p>
        </div>
        <div className="feature-card">
          <h3>Own</h3>
          <p>
            Local-first and portable. Your data stays on your machine; export
            and share your knowledge as shareable packages. No cloud lock-in.
          </p>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Section: Feature grids                                             */
/* ------------------------------------------------------------------ */

interface Feature {
  title: string;
  description: string;
  linkTo: string;
}

const FEATURE_GROUPS: { label: string; items: Feature[] }[] = [
  {
    label: "Core Intelligence",
    items: [
      {
        title: "Knowledge Graph",
        description:
          "See your knowledge. Extract entities and relationships from your documents and explore them on an interactive, inspectable graph canvas — every answer traces back to its sources.",
        linkTo: "/docs/user-guide/knowledge-graph",
      },
      {
        title: "GraphRAG Search",
        description:
          "Find answers from across your whole library. Fuses knowledge graph traversal with vector search using Personalized PageRank and Reciprocal Rank Fusion — plus keyword, semantic, and hybrid search modes.",
        linkTo: "/docs/user-guide/search",
      },
      {
        title: "AI Chat with RAG",
        description:
          "Ask questions and get cited answers grounded in your actual content — see exactly which sources and connections produced each answer, scoped to specific sources or the full database.",
        linkTo: "/docs/user-guide/chat",
      },
    ],
  },
  {
    label: "Data Foundation",
    items: [
      {
        title: "Quality Analysis",
        description:
          "Score the richness and completeness of your knowledge graph on a 0-100 scale. Detailed breakdowns by entity quality, relationship density, connectivity, and coverage — identify weak sources and guide improvement.",
        linkTo: "/docs/user-guide/quality",
      },
      {
        title: "Document Processing",
        description:
          "Upload PDFs, Word documents, web pages, and more. Automatic chunking, embedding, and RAG-ready indexing.",
        linkTo: "/docs/user-guide/sources",
      },
      {
        title: "Multi-LLM Support",
        description:
          "Connect to Ollama, OpenAI, Anthropic, or Gemini. Run fully local with Ollama or use cloud providers — switch between them with a single config change. Embeddings run locally on CPU or GPU, or in the cloud.",
        linkTo: "/docs/getting-started/configuration",
      },
    ],
  },
  {
    label: "Automation & Integration",
    items: [
      {
        title: "Automations",
        description:
          "Build multi-step workflows with triggers, conditional logic, and a visual workflow builder. Execute automated knowledge extraction pipelines.",
        linkTo: "/docs/user-guide/automations",
      },
      {
        title: "MCP Server",
        description:
          "Connect Claude Desktop, Cursor, ChatGPT, and other AI assistants directly to your knowledge graph via the Model Context Protocol. 31 tools for search, traversal, and graph building.",
        linkTo: "/docs/user-guide/mcp",
      },
      {
        title: "Plugin System",
        description:
          "Extend Chaos Cypher with custom document loaders, extraction domains, and workflow tools. Drop a Python file into the plugins directory — no registration needed.",
        linkTo: "/docs/user-guide/domains",
      },
    ],
  },
];

function FeatureCard({ feature }: { feature: Feature }) {
  return (
    <div className="feature-card">
      <h3>{feature.title}</h3>
      <p>{feature.description}</p>
      <Link className="feature-card-link" to={feature.linkTo}>
        Learn more<ArrowRight />
      </Link>
    </div>
  );
}

function FeatureGrids() {
  return (
    <section>
      {FEATURE_GROUPS.map((group) => (
        <Fragment key={group.label}>
          <p className="feature-row-label">{group.label}</p>
          <div className="feature-grid">
            {group.items.map((feature) => (
              <FeatureCard key={feature.title} feature={feature} />
            ))}
          </div>
        </Fragment>
      ))}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Section: Integrations strip                                        */
/* ------------------------------------------------------------------ */

interface BadgeGroupProps {
  label: string;
  badges: { text: string; to?: string; extra?: string; title?: string }[];
}

function BadgeGroup({ label, badges }: BadgeGroupProps) {
  return (
    <div className="integrations-group">
      <div className="integrations-group-label">
        <strong>{label}</strong>
      </div>
      <div className="integration-badges">
        {badges.map((b) => {
          const className = `integration-badge${b.extra ? ` ${b.extra}` : ""}`;
          return b.to ? (
            <Link key={b.text} className={className} to={b.to} title={b.title}>
              {b.text}
            </Link>
          ) : (
            <span key={b.text} className={className} title={b.title}>
              {b.text}
            </span>
          );
        })}
      </div>
    </div>
  );
}

function IntegrationsStrip() {
  return (
    <section className="integrations-section">
      <div className="integrations-strip">
        <BadgeGroup
          label="Integrations"
          badges={[
            { text: "MCP", to: "/docs/user-guide/mcp" },
            { text: "REST API", to: "/docs/reference/api" },
            { text: "Python SDK", to: "/docs/developer-guide/quickstart" },
            { text: "CLI", to: "/docs/reference/cli" },
          ]}
        />
        <BadgeGroup
          label="LLM Providers"
          badges={[
            { text: "Ollama", to: "/docs/getting-started/configuration" },
            { text: "Anthropic", to: "/docs/getting-started/configuration" },
            { text: "Gemini", to: "/docs/getting-started/configuration" },
            { text: "OpenAI", to: "/docs/getting-started/configuration" },
          ]}
        />
        <BadgeGroup
          label="Formats"
          badges={[
            { text: "PDF", to: "/docs/user-guide/sources" },
            { text: "DOCX", to: "/docs/user-guide/sources" },
            { text: "MP3", to: "/docs/user-guide/sources" },
            { text: "MP4", to: "/docs/user-guide/sources" },
            { text: "PNG", to: "/docs/user-guide/sources" },
            {
              text: "30+ more",
              to: "/docs/user-guide/sources",
              title:
                "30+ formats, auto-detected — including TXT, CSV, JSON, HTML, MD, EPUB, WAV, FLAC, MKV, MOV, WebP, and ZIP archives.",
            },
          ]}
        />
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Section: Get Started pathway cards                                 */
/* ------------------------------------------------------------------ */

function GetStarted() {
  return (
    <section>
      <h2>Get Started</h2>
      <p>Pick the path that fits how you work:</p>

      <div className="pathway-grid">
        <div className="pathway-card" id="install-docker">
          <h3>Docker</h3>
          <p className="pathway-audience">
            Full web UI — the complete command (named container, HTTPS-ready). The
            homepage one-liner is the quickest HTTP-only try.
          </p>
          <CodeBlock language="bash">
            {`docker run -d --name chaoscypher \\\n  -p 80:80 \\\n  -p 443:443 \\\n  -v chaoscypher-data:/data \\\n  ghcr.io/chaoscypherinc/chaoscypher:latest`}
          </CodeBlock>
          <details className="pathway-compose">
            <summary>Prefer Docker Compose?</summary>
            <p>Save as <code>docker-compose.yml</code> and run <code>docker compose up -d</code>:</p>
            <CodeBlock language="yaml">{`name: chaoscypher
services:
  chaoscypher:
    image: ghcr.io/chaoscypherinc/chaoscypher:latest
    container_name: chaoscypher
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - chaoscypher-data:/data
    restart: unless-stopped
volumes:
  chaoscypher-data:`}</CodeBlock>
          </details>
          <ul>
            <li>Web UI with graph canvas</li>
            <li>REST API + queue monitor</li>
            <li>Background workers</li>
            <li>Hot reload for development</li>
          </ul>
          <Link to="/docs/getting-started/installation">
            Full installation guide
            <ArrowRight />
          </Link>
        </div>

        <div className="pathway-card" id="install-cli">
          <h3>CLI</h3>
          <p className="pathway-audience">
            Terminal-first — process documents and query your graph from the
            shell
          </p>
          <CodeBlock language="bash">
            {`pipx install chaoscypher-cli\nchaoscypher setup\nchaoscypher source add paper.pdf`}
          </CodeBlock>
          <ul>
            <li>Setup wizard for LLM config</li>
            <li>Add, search, and manage sources</li>
            <li>Graph and template operations</li>
            <li>Interactive chat sessions</li>
          </ul>
          <Link to="/docs/reference/cli">
            CLI reference
            <ArrowRight />
          </Link>
        </div>

        <div className="pathway-card pathway-wide" id="install-python">
          <h3>Python Package</h3>
          <p className="pathway-audience">
            Integrate into your own code — extract, search, build graphs
            programmatically
          </p>
          <div className="pathway-wide-inner">
            <div>
              <CodeBlock language="python">
                {`from chaoscypher_core import ChaosCypher\n\nresult = ChaosCypher.extract_sync("paper.pdf")\nprint(result.model_dump_json(indent=2))`}
              </CodeBlock>
            </div>
            <div>
              <ul>
                <li>Zero boilerplate, one-liner API</li>
                <li>Pydantic models throughout</li>
                <li>Sync and async interfaces</li>
                <li>Embeddable Engine class</li>
              </ul>
            </div>
          </div>
          <Link to="/docs/developer-guide/quickstart">
            Developer quickstart
            <ArrowRight />
          </Link>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Section: Final CTA — built for builders                           */
/* ------------------------------------------------------------------ */

function FinalCTA({ githubUrl }: { githubUrl: string }) {
  return (
    <section className="final-cta">
      <h2>Built for builders</h2>
      <p className="final-cta-sub">
        A modular monorepo with a framework-agnostic Core — run it behind the
        API, inside workers, or embedded in your own scripts. Open source, local
        or cloud, no API keys needed to start.
      </p>
      <div className="hero-buttons">
        <Link
          className="hero-btn-primary"
          to="/docs/getting-started/quickstart"
        >
          Get Started
        </Link>
        <a className="hero-btn-secondary" href={githubUrl}>
          Star on GitHub
        </a>
      </div>
      <p className="final-cta-arch-link">
        <Link to="/docs/architecture/overview">
          Explore the architecture<ArrowRight />
        </Link>
      </p>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Page root                                                          */
/* ------------------------------------------------------------------ */

export default function Home(): JSX.Element {
  const { siteConfig } = useDocusaurusContext();
  const githubUrl = (siteConfig.customFields?.github as { url: string }).url;

  return (
    <Layout
      title="Home"
      description="Knowledge you can see, trust, and own — a local-first, inspectable knowledge graph for your documents."
    >
      <GraphHero />
      <main className="container margin-vert--lg">
        <ValueBand />
        <hr />
        <ScreenshotStrip shots={SHOWCASE} />
        <hr />
        <FeatureGrids />
        <hr />
        <IntegrationsStrip />
        <hr />
        <GetStarted />
        <hr />
        <FinalCTA githubUrl={githubUrl} />
      </main>
    </Layout>
  );
}
