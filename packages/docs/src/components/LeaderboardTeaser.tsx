import Link from "@docusaurus/Link";
import data from "../data/leaderboard.json";
import { boardCorpora, boardCorpus } from "../lib/boardCorpora";
import {
  comparisonShows,
  headlineHolds,
  readProvenance,
} from "../lib/leaderboardProvenance";
import styles from "./LeaderboardTeaser.module.css";

/** The exporter's interval [lo, hi] from resampling the row's tasks; absent in older exports. */
type Interval = [number, number] | null;
type Scores = {
  extraction: number | null;
  chat: number | null;
  overall: number | null;
  overall_ci90?: Interval;
};
type Model = {
  id: string;
  label: string;
  vram_gb: number | null;
  harness?: string | null;
  carrier?: { passed: number; total: number };
  scores: Scores;
};

const WEIGHTS = data.score_weights.overall;
const CHAT_TOTAL = data.suites.chat.total;
// The corpus the table's chat column and Overall rest on.
const BOARD_LABEL = boardCorpus(data)?.label;
const TOP = 5;
/** The retrieval comparison's lift line needs this many models run under all three rungs. */
const COMPARISON_MIN_MODELS = 3;

type Lift = { value: number; ci90: [number, number] };
type ComparisonModel = { lift: Record<string, { naive: Lift; hybrid: Lift }> };
type Headline = { key: string; holds: boolean; text: string };
type Comparison = {
  models: ComparisonModel[];
  questions: { id: string }[];
  value?: { headlines: Headline[] };
};
// Each corpus's comparison, on its own; absent until a model has rows under all three rungs.
const corpora = boardCorpora<{ comparison?: Comparison }>(data);
// The same reading as the full page: unknown or overridden provenance is provisional.
const provenance = readProvenance(data);

/** The median of a list, or null for an empty one. */
function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/**
 * The homepage's one line on one corpus's retrieval comparison, naming the corpus,
 * once enough models have all three rungs on it. Each corpus is reported on its own.
 */
function graphAdvantageLine(
  cmp: Comparison | undefined,
  label: string,
): string | null {
  if (!cmp || cmp.models.length < COMPARISON_MIN_MODELS) return null;
  // The exporter's headlines carry `holds`: the first that clears its bar is the line,
  // never on a provisional board.
  const headline = cmp.value?.headlines.find((h) =>
    headlineHolds(h, provenance),
  );
  if (headline) return `On ${label}: ${headline.text}.`;
  // The median fallback is a claim too: it shows only when the comparison does.
  if (!comparisonShows(provenance)) return null;
  const lift = median(
    cmp.models.map((m) => m.lift.graph_advantage?.naive.value ?? 0),
  );
  if (lift == null) return null;
  const n = cmp.questions.length;
  return `On ${label}, on ${n} questions written to need a graph, graph + vector answers a median ${lift} more right than plain vector RAG across ${cmp.models.length} models.`;
}

// One line per corpus whose comparison has something to say.
const GRAPH_LINES = corpora
  .map((c) => graphAdvantageLine(c.suite?.comparison, c.label))
  .filter((line): line is string => line != null);
// Overall needs both suites: the extraction probes and the grounded-chat probes.
const REPRO_CMD =
  "chaoscypher benchmark run probes --local-only && chaoscypher benchmark run chat-probes --local-only";

const pct = (v: number) => `${Math.round(v)}%`;
const share = (w: number) => `${Math.round(100 * w)}%`;

/** The row's Overall interval, or null when the export carries none. */
function interval(m: Model): [number, number] | null {
  const ci = m.scores.overall_ci90;
  return ci && ci.length === 2 ? [ci[0], ci[1]] : null;
}

/** Two rows whose intervals share a value cannot be ordered by this board. */
function overlaps(
  a: [number, number] | null,
  b: [number, number] | null,
): boolean {
  return a != null && b != null && a[0] <= b[1] && b[0] <= a[1];
}

/** Competition ranking: a row within the interval of the row above shares that row's rank. */
function ranks(rows: Model[]): { rank: number; tied: boolean }[] {
  let rank = 1;
  return rows.map((m, i) => {
    const tied = i > 0 && overlaps(interval(m), interval(rows[i - 1]));
    if (!tied) rank = i + 1;
    return { rank, tied };
  });
}

function ArrowRight() {
  return <span aria-hidden="true"> &#8594;</span>;
}

/** The first checkable claim on the homepage: the top of the leaderboard by Overall. */
export default function LeaderboardTeaser() {
  // Pinned rows only: harness-track rows (a model run through an MCP client on
  // its own settings) have their own group on the full page.
  const pinned = (data.models as Model[]).filter((m) => m.harness == null);
  const rows = pinned
    .filter((m) => m.scores.overall != null)
    .sort(
      (a, b) =>
        b.scores.overall! - a.scores.overall! ||
        (b.carrier?.passed ?? 0) - (a.carrier?.passed ?? 0),
    )
    .slice(0, TOP);
  const ranked = ranks(rows);

  return (
    <section
      className={styles.section}
      id="leaderboard"
      aria-labelledby="leaderboard-heading"
    >
      <p className="feature-row-label">Model leaderboard</p>
      <h2 id="leaderboard-heading">
        Pick a local model that does the whole job.
      </h2>
      <p className={styles.lede}>
        Every model here ran the real extraction pipeline, then answered{" "}
        {CHAT_TOTAL} grounded questions from one reference graph
        {BOARD_LABEL ? ` of ${BOARD_LABEL}` : ""}. Overall is{" "}
        {share(WEIGHTS.extraction)} extraction and {share(WEIGHTS.chat)}{" "}
        grounded chat{BOARD_LABEL ? ` on ${BOARD_LABEL}` : ""}. Top {TOP} of{" "}
        {pinned.length} models, last run {data.last_run}. A rank with ≈ is a tie: the row's interval overlaps
        the one above.{" "}
        {GRAPH_LINES.map((line) => `${line} `).join("")}
        {provenance.provisional
          ? provenance.known
            ? `Provisional: published with ${provenance.overrides.join(", ")}. `
            : "Provisional: this export does not record what scored it. "
          : ""}
        <Link to="/docs/reference/extraction-benchmark">
          How it is scored
          <ArrowRight />
        </Link>
      </p>

      <div className={styles.board}>
        <table
          className={styles.table}
          aria-label={`Top ${TOP} local models by Overall score`}
        >
          <thead>
            <tr>
              <th scope="col" className={styles.rank}>
                <abbr title="Rank">#</abbr>
              </th>
              <th scope="col" className={styles.model}>
                Model
              </th>
              <th scope="col" className={styles.overall}>
                Overall
              </th>
              <th scope="col" className={`${styles.num} ${styles.wide}`}>
                Extraction
              </th>
              <th scope="col" className={`${styles.num} ${styles.wide}`}>
                Chat
              </th>
              <th scope="col" className={styles.num}>
                VRAM
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m, i) => (
              <tr key={m.id} className={i === 0 ? styles.first : undefined}>
                <td className={styles.rank}>
                  {ranked[i].tied ? "≈" : ""}
                  {ranked[i].rank}
                </td>
                <td className={styles.model}>{m.label}</td>
                <td className={styles.overall}>
                  <span className={styles.meter}>
                    <span className={styles.value}>
                      {pct(m.scores.overall!)}
                      {interval(m) && (
                        <span className={styles.ci}>
                          ±{Math.round((interval(m)![1] - interval(m)![0]) / 2)}
                        </span>
                      )}
                    </span>
                    <span className={styles.bar} aria-hidden="true">
                      <i
                        style={{ width: `${Math.round(m.scores.overall!)}%` }}
                      />
                    </span>
                  </span>
                </td>
                <td className={`${styles.num} ${styles.wide}`}>
                  {pct(m.scores.extraction!)}
                </td>
                <td className={`${styles.num} ${styles.wide}`}>
                  {pct(m.scores.chat!)}
                </td>
                <td className={styles.num}>
                  {m.vram_gb != null ? `${m.vram_gb} GB` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className={styles.foot}>
          <Link className="feature-card-link" to="/leaderboard">
            Full leaderboard, filtered by the VRAM you have
            <ArrowRight />
          </Link>
          <p className={styles.repro}>
            Reproduce it: <code>{REPRO_CMD}</code>
          </p>
        </div>
      </div>
    </section>
  );
}
