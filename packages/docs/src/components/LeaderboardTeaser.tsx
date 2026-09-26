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

const TOP = 5;

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
    <section className={styles.teaser}>
      <h2>Which local model does the whole job?</h2>
      <p>
        We do not make models, so we have no favourite. Every model on the leaderboard ran the real
        extraction pipeline against pass/fail instruction probes, then the same instructions inside
        production-sized chunks, then answered 80 questions from one fixed reference graph. Overall is{" "}
        {Math.round(100 * WEIGHTS.extraction)}% extraction and {Math.round(100 * WEIGHTS.chat)}% grounded chat. Top {TOP} of {pinned.length}, last run {data.generated}:
      </p>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>Model</th>
            <th>Overall</th>
            <th>Extraction</th>
            <th>Chat</th>
            <th>VRAM</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => (
            <tr key={m.id}>
              <td>{m.label}</td>
              <td>{Math.round(m.scores.overall!)}%</td>
              <td>{Math.round(m.scores.extraction!)}%</td>
              <td>{Math.round(m.scores.chat!)}%</td>
              <td>{m.vram_gb != null ? `${m.vram_gb} GB` : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p>
        <Link to="/leaderboard">Full leaderboard, filtered by the VRAM you have</Link>
        {" · "}
        <code>chaoscypher benchmark run probes --local-only</code> reproduces it.
      </p>
    </section>
  );
}
