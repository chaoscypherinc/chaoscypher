import Link from "@docusaurus/Link";
import data from "../data/leaderboard.json";
import { heroSections } from "../lib/heroStats";

// Read once at build time: the board is static data. One section per corpus
// with a holding headline; each corpus is reported on its own.
const SECTIONS = heroSections(data);

/**
 * The hero's benchmark row: for each corpus, up to three figures from its
 * holding headlines and one line on how they were measured, naming the
 * corpus, then links to the method and the full board. A row is headed by
 * its corpus when there are several, or when no method line names it.
 * Renders nothing when no headline holds.
 */
export default function HeroStats(): JSX.Element | null {
  if (SECTIONS.length === 0) return null;
  return (
    <section className="hero-stats" aria-label="Benchmark results">
      {SECTIONS.map((section, i) => (
        <div key={section.corpus} className="hero-stats-corpus">
          {section.heading && (
            <p className="hero-stats-corpus-label">{section.heading}</p>
          )}
          <ul className="hero-stats-row">
            {section.stats.map((s) => (
              <li key={s.key} className="hero-stat">
                <span className="hero-stat-figure">{s.figure}</span>
                <span className="hero-stat-label">{s.label}</span>
                <span className="hero-stat-detail">{s.detail}</span>
              </li>
            ))}
          </ul>
          {(section.method || i === SECTIONS.length - 1) && (
            <p className="hero-stats-method">
              {section.method && (
                <>
                  {section.method.models} local models via Ollama,{" "}
                  {section.method.questions} questions on {section.label}, each
                  answered three ways: graph + vector, plain vector RAG
                  (LlamaIndex&rsquo;s standard pipeline) and hybrid search.
                </>
              )}
              {i === SECTIONS.length - 1 && (
                <>
                  {section.method && " "}
                  <Link to="/leaderboard">See every model</Link>
                  {" · "}
                  <Link to="/docs/reference/extraction-benchmark#where-the-graph-matters-the-three-rung-retrieval-comparison">
                    How we measured
                  </Link>
                </>
              )}
            </p>
          )}
        </div>
      ))}
    </section>
  );
}
