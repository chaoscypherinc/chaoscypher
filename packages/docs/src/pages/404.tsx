import React from "react";
import Layout from "@theme/Layout";
import Link from "@docusaurus/Link";
import styles from "./404.module.css";

const TOP_DESTINATIONS = [
  { label: "Getting started", to: "/docs/getting-started/overview" },
  { label: "Installation", to: "/docs/getting-started/installation" },
  { label: "API reference", to: "/docs/reference/api" },
  { label: "Architecture overview", to: "/docs/architecture/overview" },
  { label: "Glossary", to: "/docs/glossary" },
];

const RECENT_RENAMES: Array<[string, string]> = [
  ["/docs/api/...", "/docs/reference/api/..."],
  ["/docs/cli/...", "/docs/reference/cli/..."],
  ["/docs/adr/...", "/docs/architecture/adrs/..."],
];

export default function NotFound(): JSX.Element {
  return (
    <Layout title="Not found">
      <main className={styles.container}>
        <h1>404 — page not found</h1>
        <p>The page you're looking for moved or never existed.</p>
        <h2>Top destinations</h2>
        <ul>
          {TOP_DESTINATIONS.map((d) => (
            <li key={d.to}><Link to={d.to}>{d.label}</Link></li>
          ))}
        </ul>
        <h2>Recent URL changes</h2>
        <ul>
          {RECENT_RENAMES.map(([from, to]) => (
            <li key={from}><code>{from}</code> → <code>{to}</code></li>
          ))}
        </ul>
        <p>Or use search (top-right).</p>
      </main>
    </Layout>
  );
}
