import type { Config } from "@docusaurus/types";
import type * as Preset from "@docusaurus/preset-classic";
import neonNoirTheme from "./src/prismTheme";

const config: Config = {
  title: "Chaos Cypher",
  tagline: "Decode knowledge from chaos — AI-powered knowledge graph engine",
  url: "https://chaoscypher.com",
  baseUrl: "/",
  trailingSlash: false,
  onBrokenLinks: "throw",
  onBrokenAnchors: "throw",
  organizationName: "chaoscypherinc",
  projectName: "chaoscypher",
  favicon: "img/favicon-192.png",
  headTags: [
    {
      tagName: "link",
      attributes: {
        rel: "apple-touch-icon",
        sizes: "180x180",
        href: "/img/apple-touch-icon.png",
      },
    },
    {
      tagName: "link",
      attributes: {
        rel: "icon",
        type: "image/png",
        sizes: "32x32",
        href: "/img/favicon-32.png",
      },
    },
  ],
  i18n: { defaultLocale: "en", locales: ["en"] },

  markdown: {
    mermaid: true,
    hooks: {
      onBrokenMarkdownLinks: "throw",
    },
  },

  plugins: [
    [
      "@docusaurus/plugin-client-redirects",
      {
        redirects: [],
        createRedirects(existingPath: string) {
          if (existingPath.startsWith("/docs/reference/api/")) {
            return [existingPath.replace("/docs/reference/api/", "/docs/api/")];
          }
          if (existingPath.startsWith("/docs/reference/cli/")) {
            return [existingPath.replace("/docs/reference/cli/", "/docs/cli/")];
          }
          if (existingPath.startsWith("/docs/architecture/adrs/")) {
            return [existingPath.replace("/docs/architecture/adrs/", "/docs/adr/")];
          }
          return undefined;
        },
      },
    ],
  ],

  themes: [
    "@docusaurus/theme-mermaid",
    [
      "@easyops-cn/docusaurus-search-local",
      {
        hashed: true,
        indexDocs: true,
        indexBlog: true,
      },
    ],
  ],

  presets: [
    [
      "classic",
      {
        docs: {
          sidebarPath: "./sidebars.ts",
          numberPrefixParser: false,
          showLastUpdateTime: true,
          showLastUpdateAuthor: false,
          editUrl: "https://github.com/chaoscypherinc/chaoscypher/edit/main/packages/docs/",
        },
        blog: {
          showReadingTime: true,
          blogTitle: "Blog",
          blogDescription:
            "Chaos Cypher updates, guides, and tutorials",
          postsPerPage: 10,
          blogSidebarCount: "ALL",
          editUrl: "https://github.com/chaoscypherinc/chaoscypher/edit/main/packages/docs/",
          feedOptions: {
            type: "all" as const,
            copyright: `Copyright ${new Date().getFullYear()} Chaos Cypher, Inc.`,
          },
        },
        theme: {
          customCss: ["./src/css/custom.css"],
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    image: "img/og-default.png",
    metadata: [
      { name: "twitter:card", content: "summary_large_image" },
    ],
    colorMode: {
      defaultMode: "dark",
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: "Chaos Cypher",
      logo: { alt: "Chaos Cypher Logo", src: "img/logo.png" },
      items: [
        { to: "/blog", label: "Blog", position: "left" },
        {
          type: "docSidebar",
          sidebarId: "userGuide",
          label: "User Guide",
          position: "left",
        },
        {
          type: "docSidebar",
          sidebarId: "developerGuide",
          label: "Developer Guide",
          position: "left",
        },
        {
          type: "docSidebar",
          sidebarId: "lexiconHub",
          label: "Lexicon Hub",
          position: "left",
          title: "Lexicon Hub",
        },
        {
          type: "dropdown",
          label: "Resources",
          position: "left",
          items: [
            {
              type: "docSidebar",
              sidebarId: "reference",
              label: "Reference",
            },
            {
              type: "docSidebar",
              sidebarId: "architecture",
              label: "Architecture",
            },
            {
              type: "docSidebar",
              sidebarId: "about",
              label: "About",
            },
            {
              href: "https://github.com/chaoscypherinc/chaoscypher/discussions",
              label: "Discussions",
            },
            {
              href: "https://github.com/chaoscypherinc/chaoscypher/issues",
              label: "Issues",
            },
          ],
        },
        {
          href: "https://github.com/chaoscypherinc/chaoscypher",
          position: "right",
          className: "navbar-github-link",
          "aria-label": "GitHub repository",
        },
      ],
    },
    footer: {
      style: "dark",
      links: [
        {
          title: "Docs",
          items: [
            { label: "Getting Started", to: "/docs/getting-started/overview" },
            { label: "User Guide", to: "/docs/user-guide/sources" },
            { label: "API Reference", to: "/docs/reference/api" },
          ],
        },
        {
          title: "Community",
          items: [
            {
              label: "GitHub",
              href: "https://github.com/chaoscypherinc/chaoscypher",
            },
            {
              label: "Issues",
              href: "https://github.com/chaoscypherinc/chaoscypher/issues",
            },
            {
              label: "Discussions",
              href: "https://github.com/chaoscypherinc/chaoscypher/discussions",
            },
            {
              label: "Lexicon Hub",
              href: "https://lexicon.chaoscypher.com",
            },
          ],
        },
        {
          title: "More",
          items: [
            { label: "Blog", to: "/blog" },
            { label: "Changelog", to: "/docs/about/changelog" },
            { label: "Roadmap", to: "/docs/about/roadmap" },
          ],
        },
      ],
      copyright: `Copyright ${new Date().getFullYear()} Chaos Cypher, Inc.`,
    },
    mermaid: {
      theme: { light: "neutral", dark: "base" },
      options: {
        themeVariables: {
          // Backgrounds
          primaryColor: "#12121e",
          primaryBorderColor: "#7b2ff7",
          primaryTextColor: "#e0e0f0",
          secondaryColor: "#0d0d1a",
          secondaryBorderColor: "#ff2d95",
          secondaryTextColor: "#e0e0f0",
          tertiaryColor: "#0a0a14",
          tertiaryBorderColor: "#00fff0",
          tertiaryTextColor: "#e0e0f0",
          // Lines and text
          lineColor: "#505068",
          textColor: "#c8c8e0",
          // Notes
          noteBkgColor: "#14142a",
          noteBorderColor: "#7b2ff7",
          noteTextColor: "#9090b0",
          // Flowchart
          nodeBorder: "#7b2ff7",
          mainBkg: "#12121e",
          clusterBkg: "#0a0a14",
          clusterBorder: "#1e1e30",
          titleColor: "#e0e0f0",
          edgeLabelBackground: "#0a0a14",
          // Fonts
          fontFamily: '"Inter", system-ui, sans-serif',
          fontSize: "14px",
        },
      },
    },
    prism: {
      theme: neonNoirTheme,
      darkTheme: neonNoirTheme,
      additionalLanguages: [
        "bash",
        "python",
        "yaml",
        "toml",
        "json",
        "nginx",
      ],
    },
  } satisfies Preset.ThemeConfig,

  customFields: {
    github: {
      url: "https://github.com/chaoscypherinc/chaoscypher",
      issuesUrl: "https://github.com/chaoscypherinc/chaoscypher/issues",
      discussionsUrl: "https://github.com/chaoscypherinc/chaoscypher/discussions",
      repo: "chaoscypher",
    },
    lexicon: {
      url: "https://lexicon.chaoscypher.com",
      name: "Lexicon Hub",
    },
  },
};

export default config;
