import type { SidebarsConfig } from "@docusaurus/plugin-content-docs";

const sidebars: SidebarsConfig = {
  userGuide: [
    {
      type: "category",
      label: "Getting Started",
      items: [
        "getting-started/overview",
        "getting-started/installation",
        "getting-started/quickstart",
        "getting-started/configuration",
        "getting-started/first-login",
        "getting-started/upgrading",
        "getting-started/production",
        "getting-started/backup-restore",
        "getting-started/uninstalling",
      ],
    },
    {
      type: "category",
      label: "Data",
      items: [
        "user-guide/sources",
        "user-guide/quality",
        "user-guide/data-quality",
        "user-guide/knowledge-graph",
        "user-guide/databases",
        "user-guide/import-export",
      ],
    },
    {
      type: "category",
      label: "Features",
      items: [
        "user-guide/chat",
        "user-guide/search",
        "user-guide/search-status",
        "user-guide/mcp",
        "user-guide/automations",
      ],
    },
    {
      type: "category",
      label: "Plugins",
      items: [
        "user-guide/domains",
        "user-guide/loaders",
        "user-guide/tool-plugins",
      ],
    },
    {
      type: "category",
      label: "Security",
      items: ["security/self-hosted-threat-model", "security/plugin-trust"],
    },
    "glossary",
  ],

  lexiconHub: [
    "lexicon-hub/index",
    "lexicon-hub/discovering",
    "lexicon-hub/publishing",
    "lexicon-hub/authentication",
  ],

  developerGuide: [
    {
      type: "category",
      label: "Getting Started",
      items: [
        "developer-guide/installation",
        "developer-guide/quickstart",
        "developer-guide/core-concepts",
      ],
    },
    {
      type: "category",
      label: "Python Library",
      items: [
        "developer-guide/extraction-pipeline",
        "developer-guide/services",
        "developer-guide/storage-adapters",
        "developer-guide/llm-providers",
      ],
    },
    {
      type: "category",
      label: "Building Plugins",
      items: [
        "developer-guide/building-loaders",
        "developer-guide/building-tools",
        "developer-guide/building-domains",
      ],
    },
    {
      type: "category",
      label: "Contributing",
      items: [
        "developer-guide/contributing",
        "developer-guide/adding-features",
        "developer-guide/code-standards",
        "developer-guide/testing",
      ],
    },
  ],

  reference: [
    {
      type: "category",
      label: "CLI",
      items: [
        "reference/cli/index",
        {
          type: "category",
          label: "Getting Started",
          items: [
            "reference/cli/setup",
            "reference/cli/serve",
            "reference/cli/config",
          ],
        },
        {
          type: "category",
          label: "Data",
          items: [
            "reference/cli/graph",
            "reference/cli/sources",
            "reference/cli/database",
            "reference/cli/lexicon",
            "reference/cli/benchmark",
          ],
        },
        {
          type: "category",
          label: "Features",
          items: [
            "reference/cli/chat",
            "reference/cli/mcp",
            "reference/cli/quality",
            "reference/cli/compose",
          ],
        },
        "reference/cli/utilities",
      ],
    },
    "reference/extraction-benchmark",
    "reference/model-cards",
    "reference/filtering-modes",
    {
      type: "category",
      label: "API",
      items: [
        "reference/api/index",
        "reference/api/health",
        "reference/api/auth",
        {
          type: "category",
          label: "Graph elements",
          items: [
            "reference/api/nodes",
            "reference/api/edges",
            "reference/api/templates",
          ],
        },
        "reference/api/graph",
        "reference/api/grounding",
        {
          type: "category",
          label: "Data",
          items: [
            "reference/api/sources",
            "reference/api/search",
            "reference/api/chat",
            "reference/api/databases",
            "reference/api/exports",
          ],
        },
        {
          type: "category",
          label: "Automation",
          items: [
            "reference/api/workflows",
            "reference/api/tools",
            "reference/api/triggers",
          ],
        },
        {
          type: "category",
          label: "Analysis",
          items: [
            "reference/api/quality",
            "reference/api/quality-metrics",
            "reference/api/counts",
          ],
        },
        {
          type: "category",
          label: "Operations",
          items: ["reference/api/queue", "reference/api/pause", "reference/api/llm"],
        },
        {
          type: "category",
          label: "Diagnostics",
          items: ["reference/api/logs", "reference/api/diagnostics", "reference/api/edition"],
        },
        {
          type: "category",
          label: "Configuration",
          items: ["reference/api/settings", "reference/api/backup"],
        },
        {
          type: "category",
          label: "External",
          items: ["reference/api/lexicon"],
        },
      ],
    },
    {
      type: "category",
      label: "Python Library",
      items: [
        "reference/python/models",
        "reference/python/protocols",
        "reference/python/services",
        "reference/python/storage-adapters",
      ],
    },
  ],

  architecture: [
    {
      type: "category",
      label: "Overview",
      items: [
        "architecture/overview",
        "architecture/data-flow",
        "architecture/graph-storage",
      ],
    },
    {
      type: "category",
      label: "Packages",
      items: [
        "architecture/core",
        "architecture/cortex",
        "architecture/neuron",
      ],
    },
    {
      type: "category",
      label: "Extraction Pipeline",
      items: [
        "architecture/extraction-pipeline/overview",
        "architecture/extraction-pipeline/loading",
        "architecture/extraction-pipeline/encoding",
        "architecture/extraction-pipeline/normalization",
        "architecture/extraction-pipeline/chunking",
        "architecture/extraction-pipeline/indexing",
        "architecture/extraction-pipeline/entity-extraction",
        "architecture/extraction-pipeline/deduplication",
        "architecture/extraction-pipeline/relationships",
        "architecture/extraction-pipeline/commit",
        "architecture/extraction-pipeline/quality-counters",
      ],
    },
    {
      type: "category",
      label: "Extensibility",
      items: ["architecture/plugins"],
    },
    {
      type: "category",
      label: "ADRs",
      items: [
        "architecture/adrs/README",
        "architecture/adrs/0001-remove-discovery-and-lenses-features",
        "architecture/adrs/0002-dependency-license-policy",
        "architecture/adrs/0003-replace-pymupdf-with-pypdf",
        "architecture/adrs/0004-redis-to-valkey-migration",
        "architecture/adrs/0005-ollama-instances-only",
        "architecture/adrs/0006-re-adopt-alembic",
      ],
    },
  ],

  about: ["about/license", "about/changelog", "about/roadmap"],
};

export default sidebars;
