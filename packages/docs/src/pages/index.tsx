import Layout from "@theme/Layout";
import Link from "@docusaurus/Link";
import useDocusaurusContext from "@docusaurus/useDocusaurusContext";
import CodeBlock from "@theme/CodeBlock";
import GraphHero from "../components/GraphHero";
import Mermaid from "@theme/Mermaid";

function ArrowRight() {
  return <span aria-hidden="true"> &#8594;</span>;
}

/* ------------------------------------------------------------------ */
/*  Section: Feature grids                                             */
/* ------------------------------------------------------------------ */

interface FeatureCardProps {
  title: string;
  description: string;
  linkTo: string;
  hero?: boolean;
  children?: React.ReactNode;
}

function FeatureCard({
  title,
  description,
  linkTo,
  hero,
  children,
}: FeatureCardProps) {
  return (
    <div className={`feature-card${hero ? " feature-hero" : ""}`}>
      <h3>{title}</h3>
      <p>{description}</p>
      {children}
      <Link to={linkTo}>
        <ArrowRight />
      </Link>
    </div>
  );
}

function FeatureGrids() {
  return (
    <section>
      <p className="feature-row-label">Core Intelligence</p>
      <div className="feature-grid">
        <FeatureCard
          title="GraphRAG Search"
          description="Find answers that span multiple documents. Fuses knowledge graph traversal with vector search using Personalized PageRank and Reciprocal Rank Fusion — plus keyword, semantic, and hybrid search modes."
          linkTo="/docs/user-guide/search"
          hero
        />
        <FeatureCard
          title="Knowledge Graph"
          description="Extract entities and relationships from your documents. Explore connections through an interactive graph canvas with search, filtering, and templates."
          linkTo="/docs/user-guide/knowledge-graph"
        />
        <FeatureCard
          title="AI Chat with RAG"
          description="Ask questions about your documents with retrieval-augmented generation. Get cited answers grounded in your actual content, scoped to specific sources or the full database."
          linkTo="/docs/user-guide/chat"
        />
      </div>

      <p className="feature-row-label">Data Foundation</p>
      <div className="feature-grid">
        <FeatureCard
          title="Quality Analysis"
          description="Score the richness and completeness of your knowledge graph on a 0-100 scale. Detailed breakdowns by entity quality, relationship density, connectivity, and coverage — identify weak sources and guide improvement."
          linkTo="/docs/user-guide/quality"
        />
        <FeatureCard
          title="Document Processing"
          description="Upload PDFs, Word documents, web pages, and more. Automatic chunking, embedding, and RAG-ready indexing in seconds."
          linkTo="/docs/user-guide/sources"
        />
        <FeatureCard
          title="Multi-LLM Support"
          description="Connect to Ollama, OpenAI, Anthropic, or Gemini. Run fully local with Ollama or use cloud providers — switch between them with a single config change. Embeddings run locally on the CPU with no API keys needed."
          linkTo="/docs/getting-started/configuration"
        />
      </div>

      <p className="feature-row-label">Automation &amp; Integration</p>
      <div className="feature-grid">
        <FeatureCard
          title="Automations"
          description="Build multi-step workflows with triggers, conditional logic, and a visual workflow builder. Execute automated knowledge extraction pipelines."
          linkTo="/docs/user-guide/automations"
        />
        <FeatureCard
          title="MCP Server"
          description="Connect Claude Desktop, Cursor, ChatGPT, and other AI assistants directly to your knowledge graph via the Model Context Protocol. 31 tools for search, traversal, and graph building."
          linkTo="/docs/user-guide/mcp"
        />
        <FeatureCard
          title="Plugin System"
          description="Extend Chaos Cypher with custom document loaders, extraction domains, and workflow tools. Drop a Python file into the plugins directory — no registration needed."
          linkTo="/docs/user-guide/domains"
        />
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Section: Lexicon Hub                                               */
/* ------------------------------------------------------------------ */

function LexiconHub({ lexiconUrl }: { lexiconUrl: string }) {
  return (
    <section className="hub-section">
      <h2>Lexicon Hub</h2>
      <p className="hub-section-desc">
        Share, discover, and reuse knowledge packages from the community
      </p>

      <div className="feature-grid">
        <div className="feature-card">
          <h3>Pull</h3>
          <p>
            Download templates, entities, and workflows published by others.
            Jumpstart your project with community-built knowledge graphs.
          </p>
          <Link to="/docs/lexicon-hub/discovering">
            <ArrowRight />
          </Link>
          <CodeBlock language="bash">
            chaoscypher pull john/medical-ontology
          </CodeBlock>
        </div>

        <div className="feature-card feature-hero">
          <h3>Community Registry</h3>
          <p>
            Browse packages by keyword, author, or domain. Templates, knowledge
            graphs, and workflows — shared by the community, ready to import.
          </p>
          <Link to="/docs/lexicon-hub">
            <ArrowRight />
          </Link>
          <div className="hub-cta-wrapper">
            <a className="hub-cta" href={lexiconUrl}>
              Browse Packages
            </a>
          </div>
        </div>

        <div className="feature-card">
          <h3>Publish</h3>
          <p>
            Export your extracted knowledge as a CCX package and share it with
            the community. Public or private — your choice.
          </p>
          <Link to="/docs/lexicon-hub/publishing">
            <ArrowRight />
          </Link>
          <CodeBlock language="bash">
            chaoscypher push my-knowledge.ccx
          </CodeBlock>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Section: Integrations strip                                        */
/* ------------------------------------------------------------------ */

interface BadgeGroupProps {
  label: string;
  badges: { text: string; extra?: string; title?: string }[];
}

function BadgeGroup({ label, badges }: BadgeGroupProps) {
  return (
    <div className="integrations-group">
      <div className="integrations-group-label">
        <strong>{label}</strong>
      </div>
      <div className="integration-badges">
        {badges.map((b) => (
          <span
            key={b.text}
            className={`integration-badge${b.extra ? ` ${b.extra}` : ""}`}
            title={b.title}
          >
            {b.text}
          </span>
        ))}
      </div>
    </div>
  );
}

function IntegrationsStrip() {
  return (
    <section className="integrations-section">
      <div className="integrations-tier-label">Ecosystem</div>
      <div className="integrations-strip">
        <BadgeGroup
          label="Integrations"
          badges={[
            { text: "MCP" },
            { text: "REST API" },
            { text: "Python SDK" },
            { text: "CLI" },
          ]}
        />
        <BadgeGroup
          label="LLM Providers"
          badges={[
            { text: "Ollama" },
            { text: "Anthropic" },
            { text: "Gemini" },
            { text: "OpenAI" },
          ]}
        />
      </div>

      <div className="integrations-tier-label integrations-tier-label--spaced">
        Supported Formats
      </div>
      <div className="integrations-strip">
        <BadgeGroup
          label="Text"
          badges={[
            { text: "PDF" },
            { text: "DOCX" },
            { text: "CSV" },
            { text: "JSON" },
            { text: "HTML" },
            { text: "MD" },
            {
              text: "+8 more",
              extra: "badge-more",
              title:
                "Also supports: TXT, DOC, ODT, RTF, EPUB, JSONL, LOG, HTM",
            },
          ]}
        />
        <BadgeGroup
          label="Audio"
          badges={[
            { text: "MP3" },
            { text: "WAV" },
            { text: "FLAC" },
            { text: "OGG" },
            { text: "M4A" },
            {
              text: "+2 more",
              extra: "badge-more",
              title: "Also supports: WMA, AAC",
            },
          ]}
        />
        <BadgeGroup
          label="Video"
          badges={[
            { text: "MP4" },
            { text: "MKV" },
            { text: "MOV" },
            { text: "WebM" },
            { text: "AVI" },
            {
              text: "+2 more",
              extra: "badge-more",
              title: "Also supports: WMV, FLV",
            },
          ]}
        />
        <BadgeGroup
          label="Images"
          badges={[
            { text: "JPG" },
            { text: "PNG" },
            { text: "WebP" },
            { text: "GIF" },
            { text: "TIFF" },
            { text: "BMP" },
          ]}
        />
        <BadgeGroup
          label="Archives"
          badges={[
            { text: "ZIP" },
            { text: "TAR.GZ" },
            {
              text: "any content",
              extra: "badge-more",
              title:
                "Extracts and processes all supported file types inside. Auto-detects Sphinx, MkDocs, and OpenAPI documentation. Supports nested archives.",
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
        <div className="pathway-card">
          <h3>Docker</h3>
          <p className="pathway-audience">
            Full web UI — build locally for alpha, then upload, search, chat,
            and explore the graph
          </p>
          <CodeBlock language="bash">
            {`git clone https://github.com/chaoscypherinc/chaoscypher.git\ncd chaoscypher\nmake docker-up`}
          </CodeBlock>
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

        <div className="pathway-card">
          <h3>CLI</h3>
          <p className="pathway-audience">
            Terminal-first — process documents and query your graph from the
            shell
          </p>
          <CodeBlock language="bash">
            {`pip install chaoscypher-cli\nchaoscypher setup\nchaoscypher source add paper.pdf`}
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

        <div className="pathway-card pathway-wide">
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
/*  Section: Architecture                                              */
/* ------------------------------------------------------------------ */

function Architecture() {
  return (
    <section className="arch-section">
      <h2>Architecture</h2>
      <p>
        Modular monorepo — each package owns a single concern and communicates
        through typed interfaces. The Core library is framework-agnostic, so it
        runs identically behind the API, inside workers, or embedded in your own
        scripts.
      </p>
      <Mermaid
        value={`graph TB
    UI["Interface — React + TypeScript"]
    API["Cortex — FastAPI + VSA"]
    CORE["Core — Hexagonal Architecture"]
    NEURON["Neuron — Background Workers"]
    STORAGE["Storage — SQLite + Files"]

    UI --> API
    API --> CORE
    CORE --> NEURON
    CORE --> STORAGE

    style UI fill:#12121e,stroke:#00fff0,color:#e0e0f0
    style API fill:#12121e,stroke:#7b2ff7,color:#e0e0f0
    style CORE fill:#12121e,stroke:#ff2d95,color:#e0e0f0
    style NEURON fill:#12121e,stroke:#39ff14,color:#e0e0f0
    style STORAGE fill:#12121e,stroke:#ff6d00,color:#e0e0f0`}
      />
      <Link to="/docs/architecture/overview">
        Learn more about the architecture
        <ArrowRight />
      </Link>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Section: Final CTA                                                 */
/* ------------------------------------------------------------------ */

function FinalCTA({ discussionsUrl }: { discussionsUrl: string }) {
  return (
    <section className="final-cta">
      <h2>Start Building</h2>
      <p className="final-cta-sub">
        Open source. Run locally or in the cloud. No API keys needed to get
        started.
      </p>
      <div className="hero-buttons">
        <Link
          className="hero-btn-primary"
          to="/docs/getting-started/quickstart"
        >
          Get Started
        </Link>
        <a className="hero-btn-secondary" href={discussionsUrl}>
          Join Discussions
        </a>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Page root                                                          */
/* ------------------------------------------------------------------ */

export default function Home(): JSX.Element {
  const { siteConfig } = useDocusaurusContext();
  const discussionsUrl = (
    siteConfig.customFields?.github as { discussionsUrl: string }
  ).discussionsUrl;
  const lexiconUrl = (siteConfig.customFields?.lexicon as { url: string }).url;

  return (
    <Layout
      title="Home"
      description="Decode knowledge from chaos — AI-powered knowledge graph engine"
    >
      <GraphHero discussionsUrl={discussionsUrl} />
      <main className="container margin-vert--lg">
        <FeatureGrids />
        <hr />
        <LexiconHub lexiconUrl={lexiconUrl} />
        <hr />
        <IntegrationsStrip />
        <hr />
        <GetStarted />
        <hr />
        <Architecture />
        <hr />
        <FinalCTA discussionsUrl={discussionsUrl} />
      </main>
    </Layout>
  );
}
