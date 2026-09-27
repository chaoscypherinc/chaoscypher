import Link from "@docusaurus/Link";
import data from "../data/leaderboard.json";
import styles from "./LeaderboardTeaser.module.css";

type Scores = { extraction: number | null; chat: number | null; overall: number | null };
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
const TOP = 5;
const REPRO_CMD = "chaoscypher benchmark run probes --local-only";

const pct = (v: number) => `${Math.round(v)}%`;
const share = (w: number) => `${Math.round(100 * w)}%`;

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
    .sort((a, b) => b.scores.overall! - a.scores.overall! || (b.carrier?.passed ?? 0) - (a.carrier?.passed ?? 0))
    .slice(0, TOP);

  return (
    <section className={styles.section} id="leaderboard" aria-labelledby="leaderboard-heading">
      <p className="feature-row-label">Model leaderboard</p>
      <h2 id="leaderboard-heading">Pick a local model that does the whole job.</h2>
      <p className={styles.lede}>
        Every model here ran the real extraction pipeline, then answered {CHAT_TOTAL} grounded questions from
        one reference graph. Overall is {share(WEIGHTS.extraction)} extraction and {share(WEIGHTS.chat)} grounded
        chat. Top {TOP} of {pinned.length} models, last run {data.generated}.{" "}
        <Link to="/docs/reference/extraction-benchmark">
          How it is scored
          <ArrowRight />
        </Link>
      </p>

      <div className={styles.board}>
        <table className={styles.table} aria-label={`Top ${TOP} local models by Overall score`}>
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
                <td className={styles.rank}>{i + 1}</td>
                <td className={styles.model}>{m.label}</td>
                <td className={styles.overall}>
                  <span className={styles.meter}>
                    <span className={styles.value}>{pct(m.scores.overall!)}</span>
                    <span className={styles.bar} aria-hidden="true">
                      <i style={{ width: `${Math.round(m.scores.overall!)}%` }} />
                    </span>
                  </span>
                </td>
                <td className={`${styles.num} ${styles.wide}`}>{pct(m.scores.extraction!)}</td>
                <td className={`${styles.num} ${styles.wide}`}>{pct(m.scores.chat!)}</td>
                <td className={styles.num}>{m.vram_gb != null ? `${m.vram_gb} GB` : "—"}</td>
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
