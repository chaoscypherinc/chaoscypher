import { Fragment } from "react";
import Layout from "@theme/Layout";
import Link from "@docusaurus/Link";
import useDocusaurusContext from "@docusaurus/useDocusaurusContext";
import CodeBlock from "@theme/CodeBlock";
import GraphHero from "../components/GraphHero";
import GuidedTour from "../components/GuidedTour";

function ArrowRight() {
  return <span aria-hidden="true"> &#8594;</span>;
}

/* ------------------------------------------------------------------ */
/*  Section: See · Trust · Own value band                             */
/* ------------------------------------------------------------------ */

function ValueBand() {
  return (
    <section className="value-band">
      <h2>Knowledge you can see, trust, and own</h2>
      <div className="feature-grid">
        <div className="feature-card value-card">
          <span className="value-card-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
              <path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z" />
              <circle cx="12" cy="12" r="2.6" />
            </svg>
          </span>
          <h3>See</h3>
          <p>
            A real, navigable <span className="vb-em">knowledge graph</span> — not a
            black-box vector blob. Inspect every entity, relationship, and source
            behind an answer.
          </p>
        </div>
        <div className="feature-card value-card">
          <span className="value-card-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 3l7 2.6v5.1c0 4.3-2.9 7.4-7 8.8-4.1-1.4-7-4.5-7-8.8V5.6L12 3z" />
              <path d="M8.8 12l2.2 2.2 4.2-4.4" />
            </svg>
          </span>
          <h3>Trust</h3>
          <p>
            Source-backed provenance, not &ldquo;AI says so.&rdquo; Every answer
            traces <span className="vb-em">the exact path</span> — entities,
            relationships, evidence — back to your documents.
          </p>
        </div>
        <div className="feature-card value-card">
          <span className="value-card-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
              <rect x="3.5" y="4.5" width="17" height="6" rx="1.5" />
              <rect x="3.5" y="13" width="17" height="6" rx="1.5" />
              <path d="M7 7.5h.01M7 16h.01" />
            </svg>
          </span>
          <h3>Own</h3>
          <p>
            Self-hosted, with local embeddings — <span className="vb-em">nothing
            leaves your machine</span> by default. Export and share knowledge as
            portable packages. No cloud lock-in.
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
        <GuidedTour />
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
