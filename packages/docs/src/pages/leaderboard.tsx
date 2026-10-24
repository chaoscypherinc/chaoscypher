import { Fragment, useMemo, useState } from "react";
import type React from "react";
import Layout from "@theme/Layout";
import Link from "@docusaurus/Link";
import data from "../data/leaderboard.json";
import styles from "./leaderboard.module.css";
import { speedFill, speedLabel } from "../lib/leaderboardScore";
import { boardCorpora, boardCorpus } from "../lib/boardCorpora";
import {
  type BoardProvenance,
  comparisonShows,
  headlineHolds,
  readProvenance,
} from "../lib/leaderboardProvenance";

/** A verdict: the fraction of runs that passed the task (older files: a boolean). */
type Verdict = number | boolean;
/** The exporter's interval [lo, hi] from resampling the row's tasks; null when the board was not run. */
type Interval = [number, number] | null;

type Suite = {
  /** The mean pass count over the runs (a whole number for one run). */
  passed: number;
  total: number;
  /** How many runs the row pools; absent in older files means one. */
  runs?: number;
  p50_ms: number;
  minutes: number;
  truncated: number;
  aborted: number;
  thinking_honoured: boolean | null;
  extra_sessions?: number;
  retried_tasks?: number;
  verdicts: Record<string, Verdict>;
};

/** Grounded chat on the fixed reference graph; passed/pct are null when the run did not finish. */
type Chat = {
  passed: number | null;
  total: number | null;
  pct: number | null;
  runs?: number;
  minutes: number;
  truncated: number;
  timed_out: boolean;
  /** Null on a harness row: the client ran it on its own thinking setting. */
  thinking: boolean | null;
  unsupported_names: number | null;
  /** Cited-correctly over cited, as a percentage; null when nothing was cited or the row predates the check. */
  citation_accuracy?: number | null;
  extra_sessions?: number;
  retried_tasks?: number;
  /** One verdict per question; empty when the run did not finish. */
  verdicts?: Record<string, Verdict>;
};

type ChatQuestion = {
  id: string;
  section: string;
  tier: string;
  question: string;
  /** False when the fixture audit found the retrieved context cannot answer it: scored on no row. */
  answerable?: boolean;
};
/** The three retrieval rungs of the comparison, in the order the section shows them. */
const RUNGS = ["naive", "hybrid", "graphrag"] as const;
type Rung = (typeof RUNGS)[number];
const RUNG_LABEL: Record<Rung, string> = {
  naive: "plain vector RAG",
  hybrid: "hybrid search",
  graphrag: "graph + vector",
};
type Counts = { passed: number; total: number };
type Lift = { value: number; ci90: [number, number] };
/** One model's passes under each rung, per group, and its lifts over the two baselines. */
type ComparisonModel = {
  id: string;
  label: string;
  harness?: string | null;
  modes: Record<
    string,
    { citation_accuracy?: number | null } & Record<
      string,
      Counts | number | null | undefined
    >
  >;
  lift: Record<string, { naive: Lift; hybrid: Lift }>;
};
type ExampleRung = {
  context: string;
  answer: string;
  passed: boolean;
  citation: string | null;
};
type Example = {
  id: string;
  kind: string;
  question: string;
  gold_answer: string;
  models: {
    id: string;
    label: string;
    rungs: Partial<Record<Rung, ExampleRung>>;
  }[];
};
/** One headline the exporter wrote with whether its bar was met; the page shows only those that hold. */
type Headline = {
  key: string;
  holds: boolean;
  text: string;
  values: Record<string, unknown>;
};
type LiftStat = {
  per_model: Record<string, number>;
  mean: number | null;
  models_improved: number;
  models: number;
};
type SizePair = {
  small_label: string;
  big_label: string;
  ratio: number;
  small_graphrag_pct: number;
  big_naive_pct: number;
  beats_hybrid_too: boolean;
};
/** The banner on a provisional board: unknown provenance, or the overrides that let it through. */
function provenanceNote(p: BoardProvenance): string {
  if (!p.known) {
    return "This export does not record what scored it or which refusals it was let through: it predates that record. Treat these scores as provisional until the board is rebuilt.";
  }
  const found =
    p.findings.length > 0
      ? `${p.findings.length} finding${p.findings.length === 1 ? "" : "s"}`
      : "these rows";
  const left =
    p.dropped.length > 0
      ? `, and ${p.dropped.length} row${p.dropped.length === 1 ? " was" : "s were"} left out`
      : "";
  return `Published with ${p.overrides.join(", ")}: the exporter would otherwise have refused ${found}${left}. Treat these scores as provisional.`;
}

/** What the graph is worth, four ways, over the models with all three rungs (exporter: comparison.value). */
type Value = {
  models: number;
  only_with_graph: { ids: string[]; count: number; of: number };
  unanswered_on_naive: { ids: string[]; count: number; of: number };
  lift: {
    graph_advantage: { naive: LiftStat; hybrid: LiftStat };
    original: { naive: LiftStat; hybrid: LiftStat };
    differential: { naive: number | null; hybrid: number | null };
  };
  errors_removed: Record<
    string,
    {
      naive: { median_pct: number | null };
      hybrid: { median_pct: number | null };
    }
  >;
  size_substitution: { pairs: SizePair[]; best: SizePair | null };
  headlines: Headline[];
};
/** "Where the graph matters": written by the exporter once a model has rows under all three rungs. */
type Comparison = {
  kinds: string[];
  groups: string[];
  models: ComparisonModel[];
  value?: Value;
  examples: Example[];
  questions: { id: string; kind: string; question: string }[];
};
type ChatSuite = {
  total: number;
  fixture: string;
  retrieval_floor: number | null;
  retrieval_floor_ids?: string[];
  /** Questions the fixture audit excluded from every row. */
  excluded_ids?: string[];
  questions?: ChatQuestion[];
  /** Absent until a model has been run under all three rungs. */
  comparison?: Comparison;
};

/** The blended percentages the exporter computes, each with its interval; null where a board was not run. */
type Scores = {
  extraction: number | null;
  chat: number | null;
  overall: number | null;
  extraction_ci90?: Interval;
  chat_ci90?: Interval;
  overall_ci90?: Interval;
};

type Model = {
  id: string;
  label: string;
  vram_gb: number | null;
  license: string | null;
  open_weight: boolean | null;
  notes: string[];
  /** Whether `ollama show` lists tool calling; the app's chat loop needs it. */
  tools?: boolean | null;
  /** Set on harness-track rows: "mcp:<client>", a model run through an MCP client. */
  harness?: string | null;
  pins_applied?: boolean;
  native?: Suite;
  carrier?: Suite;
  chat?: Chat;
  scores: Scores;
};

type Probe = { id: string; section: string; tier: string; instruction: string };

const VRAM_STEPS = [8, 12, 16, 24, 32] as const;
type VramChoice = (typeof VRAM_STEPS)[number] | "any";
type SortKey = "model" | "overall" | "extraction" | "chat" | "vram" | "speed";
type SortDir = "asc" | "desc";
type Sort = { key: SortKey; dir: SortDir };

// Pinned rows (our temperature/seed/thinking) and harness rows (a model run
// through an MCP client on its own settings) share the table; a harness row is
// marked in its model cell and shows "—" where a column does not apply.
const models = data.models as Model[];
const probes = data.probes as Probe[];
// Absent until an export includes a chat run. The board corpus's: the chat column and Overall.
const chatSuite = (data.suites as { chat?: ChatSuite }).chat;
// The corpus the chat column and Overall rest on, by the name the board gives it.
const BOARD_LABEL = boardCorpus(data)?.label ?? "the reference corpus";
// The labels the export fixes: the interval level and the thinking settings each suite ran on.
const INTERVAL = `${Math.round(100 * data.interval.level)}% interval`;
const THINKING = `thinking ${data.thinking}`;
const CHAT_THINKING = `thinking ${data.chat_thinking}`;
// The 39 carrier probes split over two real chunks (3,020 and 3,492 chars; 21 and 20 cast).
const CARRIER_TEXT =
  "one of two real 3,000–3,500-character extraction groups with 20–21 named entities";
// What let this board through its refusals (the exporter's override flags) and what
// scored it. Missing or unrecorded is unknown, never clean (audit wave 5): the banner shows.
const provenance = readProvenance(data);
const chatQuestions: ChatQuestion[] = chatSuite?.questions ?? [];
const floorIds = new Set(chatSuite?.retrieval_floor_ids ?? []);
// Questions the fixture audit found unanswerable from the retrieved context: on no row's count.
const excludedIds = new Set([
  ...(chatSuite?.excluded_ids ?? []),
  ...chatQuestions.filter((q) => q.answerable === false).map((q) => q.id),
]);
const chatExcluded = excludedIds.size;
// How many questions the fixture asks, scored or not.
const chatAsked =
  chatQuestions.length > 0
    ? chatQuestions.length
    : (chatSuite?.total ?? 0) + chatExcluded;
/** "74 of 80" once questions are excluded, else the plain count. */
const chatCountLabel = (total: number) =>
  chatExcluded > 0 ? `${total} of ${chatAsked}` : `${total}`;
const CHAT_SECTION_LABEL: Record<string, string> = {
  factual_single_hop: "single-hop",
  paraphrase: "paraphrase",
  multi_hop: "multi-hop",
  fine_grained_discrimination: "fine-grained",
  out_of_scope: "out of scope",
};
// The comparison section, one per corpus, each on its own: absent from a data file
// exported before any model had all three rungs on that corpus.
// A corpus whose every comparison answer was also graded by hand shows the hand-graded
// numbers (the exporter computes its whole comparison on the reviewers' verdicts).
type CorpusComparison = {
  id: string;
  label: string;
  cmp: Comparison;
  handGraded: boolean;
};
const comparisons: CorpusComparison[] = boardCorpora<ChatSuite>(data).flatMap((c) =>
  c.suite?.comparison && c.suite.comparison.models.length > 0
    ? [{ id: c.id, label: c.label, cmp: c.suite.comparison, handGraded: c.handGraded }]
    : [],
);
const GROUP_LABEL: Record<string, string> = {
  cross_chapter_join: "cross-chapter join",
  relationship_path: "relationship path",
  cast_aggregate: "cast aggregate",
  disambiguation: "disambiguation",
  graph_advantage: "all graph questions",
};
/** How many ordinary questions one corpus's comparison scored (its "original" group). */
function originalCount(cmp: Comparison): number | null {
  const ordinary = cmp.value?.headlines.find((h) => h.key === "failures_ordinary");
  const asked = ordinary?.values.questions;
  if (typeof asked === "number") return asked;
  const counts = cmp.models[0]?.modes.graphrag?.original;
  return counts != null && typeof counts === "object" ? counts.total : null;
}
const groupLabel = (g: string, cmp?: Comparison) => {
  if (g === "original") {
    const n = cmp ? originalCount(cmp) : null;
    return n == null ? "the original questions" : `the original ${n}`;
  }
  return GROUP_LABEL[g] ?? g.replace(/_/g, " ");
};
// How the exporter blended the scores on every row (see scripts/benchmark/export_leaderboard.py).
const WEIGHTS = data.score_weights as {
  extraction: { carrier: number; native: number };
  overall: { extraction: number; chat: number };
};
const share = (w: number) => `${Math.round(100 * w)}%`;

function rate(s?: Suite): number {
  return s && s.total ? s.passed / s.total : -1;
}

/** A verdict as the fraction of runs that passed; older files hold booleans. */
function fraction(v: Verdict | undefined): number | null {
  if (v == null) return null;
  return typeof v === "boolean" ? (v ? 1 : 0) : v;
}

/** The runs a suite pools; older files have none and mean one. */
const runsOf = (s?: { runs?: number } | null): number => s?.runs ?? 1;

/** A well-formed interval, or null. */
function interval(ci?: Interval): [number, number] | null {
  return ci && ci.length === 2 ? [ci[0], ci[1]] : null;
}

/** Whether two intervals share any value: the board cannot order those rows. */
function overlaps(
  a: [number, number] | null,
  b: [number, number] | null,
): boolean {
  return a != null && b != null && a[0] <= b[1] && b[0] <= a[1];
}

const halfWidth = (ci: [number, number]) => Math.round((ci[1] - ci[0]) / 2);
const fmtInterval = (ci: [number, number]) =>
  `${Math.round(ci[0])}–${Math.round(ci[1])}`;

/** "pass", "fail" or how many runs passed, for a strip cell's title. */
function verdictWord(v: number, runs: number): string {
  if (v >= 1) return "pass";
  if (v <= 0) return "fail";
  return `passed ${Math.round(v * runs)} of ${runs} runs`;
}

/** Median ms per probe, or null when not measured (harness rows: the client runs the model). */
function p50(m: Model): number | null {
  const ms = m.carrier?.p50_ms ?? m.native?.p50_ms;
  return ms ? ms : null;
}

/** What a column sorts on; null means "not measured" and always sorts last. */
const SORT_VALUE: Record<SortKey, (m: Model) => number | string | null> = {
  model: (m) => m.label.toLowerCase(),
  overall: (m) => m.scores.overall,
  extraction: (m) => m.scores.extraction,
  chat: (m) => m.scores.chat,
  vram: (m) => m.vram_gb,
  speed: (m) => p50(m),
};

/** The direction a first click on a column gives: best first. */
const FIRST_DIR: Record<SortKey, SortDir> = {
  model: "asc",
  overall: "desc",
  extraction: "desc",
  chat: "desc",
  vram: "asc",
  speed: "asc",
};

function compare(a: Model, b: Model, { key, dir }: Sort): number {
  const va = SORT_VALUE[key](a);
  const vb = SORT_VALUE[key](b);
  if (va == null || vb == null) return va == null ? (vb == null ? 0 : 1) : -1;
  const c =
    typeof va === "string"
      ? va.localeCompare(vb as string)
      : va - (vb as number);
  return dir === "asc" ? c : -c;
}

function sortRows(rows: Model[], sort: Sort): Model[] {
  return [...rows].sort(
    (a, b) => compare(a, b, sort) || rate(b.native) - rate(a.native),
  );
}

const pct = (v: number) => `${Math.round(v)}%`;
const counts = (c?: { passed: number | null; total: number | null }) =>
  c ? `${c.passed ?? 0}/${c.total}` : "—";

/** A label with a panel that opens on hover or keyboard focus; one line per entry. */
function Tip({
  label,
  lines,
  align = "left",
  className = "",
}: {
  label: React.ReactNode;
  lines: string[];
  align?: "left" | "right";
  className?: string;
}) {
  return (
    <span className={`${styles.tip} ${className}`} tabIndex={0}>
      {label}
      <span
        className={`${styles.tipBox} ${align === "right" ? styles.tipRight : ""}`}
        role="tooltip"
      >
        {lines.map((line, i) => (
          <span key={i}>{line}</span>
        ))}
      </span>
    </span>
  );
}

/** A column header; with `sortKey` it is a button that sorts the table, a second click flips the direction. */
function Th({
  id,
  className = "",
  sortKey,
  sort,
  onSort,
  align = "left",
  children,
}: {
  id: string;
  className?: string;
  sortKey?: SortKey;
  sort?: Sort;
  onSort?: (key: SortKey) => void;
  align?: "left" | "right";
  children: React.ReactNode;
}) {
  const active = sortKey != null && sort?.key === sortKey;
  if (!sortKey || !onSort) {
    return (
      <th className={`${styles.th} ${className}`} tabIndex={0}>
        <span className={styles.hint}>{children}</span>
        <HeaderPanel id={id} align={align} />
      </th>
    );
  }
  return (
    <th
      className={`${styles.th} ${className}`}
      aria-sort={
        active ? (sort?.dir === "asc" ? "ascending" : "descending") : "none"
      }
    >
      <button
        type="button"
        className={`${styles.sortBtn} ${active ? styles.sortActive : ""}`}
        onClick={() => onSort(sortKey)}
      >
        <span className={styles.hint}>{children}</span>
        <span className={styles.sortArrow} aria-hidden="true">
          {active ? (sort?.dir === "asc" ? "↑" : "↓") : "↕"}
        </span>
      </button>
      <HeaderPanel id={id} align={align} />
    </th>
  );
}

/** The name, with the tag, license, harness and completion notes in the hover; a flag when there are notes. */
function ModelCell({ m }: { m: Model }) {
  const lines = [
    m.harness
      ? `${m.id.split("/").pop()}, ran through an MCP client (${m.harness.replace(/^mcp:/, "")}) on the client's own settings`
      : `${m.id.replace(/^ollama\//, "")}${m.license ? `, ${m.license}` : ""}`,
    ...m.notes,
  ];
  return (
    <td className={styles.model}>
      <Tip label={m.label} lines={lines} className={styles.modelName} />
      {m.notes.length > 0 && (
        <span
          className={`${styles.flag} ${styles.flagSmall} ${styles.modelFlag}`}
          role="img"
          aria-label={m.notes.join("; ")}
          title={m.notes.join("\n")}
        >
          !
        </span>
      )}
    </td>
  );
}

/** The blended score with its arithmetic and interval in the hover; "—" until every board has run. */
function Overall({ m, tied }: { m: Model; tied: boolean }) {
  const value = m.scores.overall;
  if (value == null) {
    return (
      <td
        className={styles.score}
        title="Needs the extraction probes and the grounded-chat board."
      >
        —
      </td>
    );
  }
  const e = m.scores.extraction!;
  const c = m.scores.chat!;
  const ci = interval(m.scores.overall_ci90);
  const runs = Math.max(runsOf(m.native), runsOf(m.carrier), runsOf(m.chat));
  const lines = [
    `Overall ${pct(value)} = ${share(WEIGHTS.overall.extraction)} of extraction + ${share(WEIGHTS.overall.chat)} of chat on ${BOARD_LABEL}`,
    `Extraction ${pct(e)}: ${WEIGHTS.overall.extraction} × ${e.toFixed(1)}% = ${(WEIGHTS.overall.extraction * e).toFixed(1)} points`,
    `Chat ${pct(c)}: ${WEIGHTS.overall.chat} × ${c.toFixed(1)}% = ${(WEIGHTS.overall.chat * c).toFixed(1)} points${m.chat?.timed_out ? " (the run did not finish, so chat counts as 0)" : ""}`,
    ci
      ? `${INTERVAL} ${fmtInterval(ci)} from resampling the tasks${runs > 1 ? `, mean of ${runs} runs` : ""}.`
      : "",
    ci ? "Rows whose intervals overlap are not separable by this board." : "",
    "Speed is not part of it: it depends on the GPU.",
  ].filter(Boolean);
  return (
    <td className={styles.score}>
      {tied && (
        <span
          className={styles.tie}
          role="img"
          aria-label="within the interval of the row above"
        >
          ≈
        </span>
      )}
      <Tip
        label={
          <span className={`${styles.chip} ${styles.chipOverall}`}>
            {pct(value)}
          </span>
        }
        lines={lines}
      />
      {ci && <span className={styles.ci}>±{halfWidth(ci)}</span>}
      <span className={styles.scoreBar} aria-hidden="true">
        <i style={{ width: `${Math.round(value)}%` }} />
      </span>
    </td>
  );
}

/** Extraction score; the in-chunks strip is its bar (one cell per probe, orange = failed), a plain bar when the strip is hidden. */
function Extraction({ m }: { m: Model }) {
  const value = m.scores.extraction;
  if (value == null) {
    return (
      <td
        className={styles.score}
        title="Needs both probe boards: isolated and in chunks."
      >
        —
      </td>
    );
  }
  const ci = interval(m.scores.extraction_ci90);
  const runs = Math.max(runsOf(m.native), runsOf(m.carrier));
  const lines = [
    `Extraction ${pct(value)}: does the model follow the extraction prompt, ${THINKING}`,
    `In chunks ${counts(m.carrier)}, ${pct(100 * rate(m.carrier))}: each instruction inside ${CARRIER_TEXT}. Weighted ${share(WEIGHTS.extraction.carrier)}, because this is what transfers to real data.`,
    `Isolated ${counts(m.native)}, ${pct(100 * rate(m.native))}: the same instructions alone in a short passage. Weighted ${share(WEIGHTS.extraction.native)}, as the diagnostic behind the chunk score.`,
    ci
      ? `${INTERVAL} ${fmtInterval(ci)} from resampling the probes${runs > 1 ? `, mean of ${runs} runs` : ""}.`
      : "",
    "The strips under the row show every probe: one cell each, orange = failed, half orange = failed in some runs. Hover a cell for its instruction.",
  ].filter(Boolean);
  return (
    <td className={styles.score}>
      <Tip
        label={<span className={styles.chip}>{pct(value)}</span>}
        lines={lines}
      />
      <span className={styles.scoreBar} aria-hidden="true">
        <i style={{ width: `${Math.round(value)}%` }} />
      </span>
    </td>
  );
}

/** One cell per probe or question, grouped by section; the same column is the same item on every row. */
type Cell = {
  id: string;
  /** The pass fraction; null when not scored. */
  ok: number | null;
  floor?: boolean;
  excluded?: boolean;
  title: string;
};

/** The cell's styles: failed, failed in some runs, a retrieval miss, excluded, or not scored. */
function cellClass(c: Cell): string {
  return [
    styles.cell,
    c.excluded ? styles.excluded : "",
    c.ok != null && !c.excluded && c.ok <= 0 ? styles.fail : "",
    c.ok != null && !c.excluded && c.ok > 0 && c.ok < 1 ? styles.mixed : "",
    c.floor ? styles.floor : "",
    c.ok == null && !c.excluded ? styles.unknown : "",
  ]
    .filter(Boolean)
    .join(" ");
}

function Strip({
  label,
  groups,
}: {
  label: string;
  groups: { key: string; cells: Cell[] }[];
}) {
  const empty = groups.every((g) => g.cells.length === 0);
  return (
    <span className={styles.strip}>
      <span className={styles.stripLabel}>{label}</span>
      {empty ? (
        <span className={styles.stripEmpty}>not run</span>
      ) : (
        groups.map((g) => (
          <span className={styles.stripGroup} key={g.key}>
            {g.cells.map((c) => (
              <span key={c.id} className={cellClass(c)} title={c.title} />
            ))}
          </span>
        ))
      )}
    </span>
  );
}

const SECTIONS = ["A", "B", "C", "D", "E"];

/** The detail line under a model: isolated probes, in-chunks probes and chat questions as strips. */
function DetailRow({ m }: { m: Model }) {
  const probeGroups = (suite: Suite | undefined, sections: string[]) =>
    sections.map((sec) => ({
      key: sec,
      cells: suite
        ? probes
            .filter((p) => p.section === sec && p.id in suite.verdicts)
            .map((p): Cell => {
              const ok = fraction(suite.verdicts[p.id]);
              const state =
                ok == null ? "not scored" : verdictWord(ok, runsOf(suite));
              return {
                id: p.id,
                ok,
                title: `${p.id} — ${state}: ${p.instruction}`,
              };
            })
        : [],
    }));
  const chatSections = [...new Set(chatQuestions.map((q) => q.section))];
  const v = m.chat?.verdicts ?? {};
  const chatRuns = runsOf(m.chat);
  const scored = m.chat != null && m.chat.passed != null;
  const chatGroups = chatSections.map((sec) => ({
    key: sec,
    cells: !scored
      ? []
      : chatQuestions
          .filter((q) => q.section === sec)
          .map((q): Cell => {
            const excluded = excludedIds.has(q.id);
            const ok = excluded ? null : fraction(v[q.id]);
            const floor = !excluded && floorIds.has(q.id);
            let state: string;
            if (excluded)
              state =
                "excluded: the retrieved context cannot answer it (fixture audit)";
            else if (ok == null) state = "not scored";
            else if (ok <= 0 && floor)
              state = "fail (retrieval miss: every model failed it)";
            else state = verdictWord(ok, chatRuns);
            return {
              id: q.id,
              ok,
              floor,
              excluded,
              title: `${q.id}, ${CHAT_SECTION_LABEL[q.section] ?? q.section} — ${state}: ${q.question}`,
            };
          }),
  }));
  return (
    <tr className={styles.detail}>
      <td colSpan={6}>
        <Strip label="isolated" groups={probeGroups(m.native, SECTIONS)} />
        <Strip label="in chunks" groups={probeGroups(m.carrier, ["H"])} />
        <Strip label="chat" groups={chatGroups} />
      </td>
    </tr>
  );
}

/** Grounded-chat score; counts, cut-off answers, the retrieval floor and any flags in the hover. */
function ChatCell({ m }: { m: Model }) {
  const c = m.chat;
  if (!c) {
    return (
      <td
        className={styles.score}
        title={
          m.harness
            ? "Not measured: the client runs the model."
            : "Not measured."
        }
      >
        —
      </td>
    );
  }
  const flags: string[] = [];
  const noTools = m.tools === false;
  if (noTools)
    flags.push(
      "no tool calling: the app's chat loop needs it, so this model cannot be the chat model",
    );
  if (c.timed_out)
    flags.push("did not finish: the run hit the per-model time limit");
  if (c.truncated > 0)
    flags.push(
      `${c.truncated} ${c.truncated === 1 ? "answer" : "answers"} hit the output-token cap`,
    );
  const ci = interval(m.scores.chat_ci90);
  const runs = runsOf(c);
  const floor = chatSuite?.retrieval_floor ?? 0;
  const detail = [
    c.passed != null
      ? `Chat ${pct(m.scores.chat!)}: ${c.passed}/${c.total} questions answered right from a fixed reference graph of ${BOARD_LABEL}, ${m.harness ? "on the client's own settings" : CHAT_THINKING}${runs > 1 ? `, mean of ${runs} runs` : ""}`
      : "Chat: the run did not finish, so it counts as 0",
    "Pass = names the right thing, invents nothing the retrieved context lacks, declines only when the sources really lack it.",
    ci ? `${INTERVAL} ${fmtInterval(ci)} from resampling the questions.` : "",
    c.citation_accuracy != null
      ? `Citation accuracy ${pct(c.citation_accuracy)}: of the answers that cite a chunk, this share cite one that holds the answer. Reported, not scored.`
      : "",
    chatExcluded > 0 && chatSuite
      ? `${chatExcluded} of ${chatAsked} questions are excluded by the fixture audit (the retrieved context cannot answer them), so every model is scored on ${chatSuite.total}.`
      : "",
    floor > 0 && chatSuite
      ? `${floor} more ${floor === 1 ? "question was" : "questions were"} failed by every model (retrieval floor), so the ceiling is ${chatSuite.total - floor}.`
      : "",
    c.passed != null && !m.harness
      ? `${Math.round(c.minutes)} min for the run.`
      : "",
    ...flags.map((f) => f[0].toUpperCase() + f.slice(1) + "."),
  ].filter(Boolean);
  const tip = {
    role: "img",
    "aria-label": flags.join("; "),
    title: flags.join("\n"),
  };
  const flag = noTools ? (
    <span className={`${styles.dnf} ${styles.hint}`} {...tip}>
      no tools
    </span>
  ) : flags.length > 0 ? (
    <span className={`${styles.flag} ${styles.flagSmall}`} {...tip}>
      !
    </span>
  ) : null;
  if (c.passed == null || !c.total) {
    return (
      <td className={styles.score}>
        <Tip
          label={<span className={styles.dnf}>did not finish</span>}
          lines={detail}
        />{" "}
        {flag}
      </td>
    );
  }
  const value = m.scores.chat!;
  return (
    <td className={styles.score}>
      <Tip
        label={<span className={styles.chip}>{pct(value)}</span>}
        lines={detail}
      />{" "}
      {flag}
      <span className={styles.scoreBar} aria-hidden="true">
        <i style={{ width: `${Math.round(value)}%` }} />
      </span>
    </td>
  );
}

/** Weights as a share of the chosen VRAM budget (or of a 32 GB card when any). */
function Vram({ m, budget }: { m: Model; budget: VramChoice }) {
  if (m.vram_gb == null) {
    return (
      <td
        className={`${styles.num} ${styles.hidePhone}`}
        title={
          m.harness ? "Runs in the client, not on your GPU." : "Not known."
        }
      >
        —
      </td>
    );
  }
  const scale = budget === "any" ? 32 : budget - data.vram_headroom_gb;
  const fill = Math.min(100, (100 * m.vram_gb) / scale);
  const lines = [
    `${m.vram_gb} GB of model weights.`,
    budget === "any"
      ? "The gauge shows them against a 32 GB card; pick a budget above to see them against yours."
      : `The gauge shows them against the ${scale} GB left in ${budget} GB once ${data.vram_headroom_gb} GB is kept for the context window.`,
    "The weights are not the whole footprint: the context window needs room too.",
  ];
  return (
    <td className={`${styles.gaugeCell} ${styles.hidePhone}`}>
      <Tip
        label={<span className={styles.gaugeLabel}>{m.vram_gb} GB</span>}
        lines={lines}
        align="right"
      />
      <span className={styles.gauge} aria-hidden="true">
        <i style={{ width: `${fill}%` }} />
      </span>
    </td>
  );
}

/** A word for the speed; the seconds and the run time in the hover. */
function Speed({ m }: { m: Model }) {
  const ms = p50(m);
  if (ms == null) {
    return (
      <td
        className={`${styles.num} ${styles.hideMid}`}
        title={
          m.harness
            ? "Not measured: the client runs the model."
            : "Not measured."
        }
      >
        —
      </td>
    );
  }
  const label = speedLabel(ms);
  const run = m.carrier
    ? `the ${m.carrier.total}-probe chunk run took ${Math.round(m.carrier.minutes)} min`
    : "";
  const lines = [
    `${label}: ${(ms / 1000).toFixed(1)} s median per probe on one 32 GB consumer GPU, ${THINKING}${run ? `; ${run}` : ""}.`,
    "One probe is a production-sized chunk group of about 3,000 characters.",
    "Fast is under 15 s, moderate under 45 s, slow under 120 s, very slow above. Fixed thresholds, so a label never moves when other models are added.",
  ];
  return (
    <td className={`${styles.gaugeCell} ${styles.hideMid}`}>
      <Tip
        label={<span className={styles.gaugeLabel}>{label}</span>}
        lines={lines}
        align="right"
      />
      <span
        className={`${styles.gauge} ${label === "Very slow" ? styles.gaugeLow : ""}`}
        aria-hidden="true"
      >
        <i style={{ width: `${Math.round(100 * speedFill(ms))}%` }} />
      </span>
    </td>
  );
}

/** A lift as "+3 ±2" with the interval in the title; a negative lift keeps its sign. */
function LiftCell({ lift }: { lift: Lift }) {
  const [lo, hi] = lift.ci90;
  const sign = lift.value > 0 ? "+" : "";
  const cls =
    lift.value > 0 ? styles.liftUp : lift.value < 0 ? styles.liftDown : "";
  return (
    <td
      className={`${styles.num} ${cls}`}
      title={`${INTERVAL} ${lo} to ${hi} questions (paired bootstrap over the questions)`}
    >
      {sign}
      {lift.value}
      <span className={styles.ci}>±{Math.round((hi - lo) / 2)}</span>
    </td>
  );
}

/** One count cell: passed of total. */
function CountCell({ counts }: { counts: Counts | number | null | undefined }) {
  if (counts == null || typeof counts === "number")
    return <td className={styles.num}>—</td>;
  return (
    <td className={styles.num}>
      {counts.passed}/{counts.total}
    </td>
  );
}

/** The comparison table: per model and group, passes under each rung and the lifts over both baselines. */
function ComparisonTable({ cmp }: { cmp: Comparison }) {
  return (
    <table className={`${styles.table} ${styles.cmpTable}`}>
      <thead>
        <tr>
          <th className={styles.th}>Model</th>
          <th className={styles.th}>Questions</th>
          {RUNGS.map((r) => (
            <th key={r} className={`${styles.th} ${styles.num}`}>
              {RUNG_LABEL[r]}
            </th>
          ))}
          <th className={`${styles.th} ${styles.num}`}>vs plain RAG</th>
          <th className={`${styles.th} ${styles.num}`}>vs hybrid</th>
        </tr>
      </thead>
      <tbody>
        {cmp.models.map((m) => (
          <Fragment key={m.id}>
            {cmp.groups.map((g, i) => (
              <tr key={g} className={i === 0 ? styles.cmpFirst : ""}>
                {i === 0 && (
                  <td className={styles.cmpModel} rowSpan={cmp.groups.length}>
                    {m.label}
                    {m.modes.graphrag?.citation_accuracy != null && (
                      <span className={styles.cmpCite}>
                        citations right{" "}
                        {pct(m.modes.graphrag.citation_accuracy)}
                      </span>
                    )}
                  </td>
                )}
                <td
                  className={
                    g === "graph_advantage" || g === "original"
                      ? styles.cmpGroupTotal
                      : styles.cmpGroup
                  }
                >
                  {groupLabel(g, cmp)}
                </td>
                {RUNGS.map((r) => (
                  <CountCell key={r} counts={m.modes[r]?.[g]} />
                ))}
                <LiftCell lift={m.lift[g].naive} />
                <LiftCell lift={m.lift[g].hybrid} />
              </tr>
            ))}
          </Fragment>
        ))}
      </tbody>
    </table>
  );
}

/** One worked example: the question, the gold answer, then each rung's context and answer per reference model. */
function ExampleCard({ example, cmp }: { example: Example; cmp: Comparison }) {
  return (
    <article className={styles.exampleCard}>
      <h3 className={styles.exampleTitle}>
        <span className={styles.exampleKind}>{groupLabel(example.kind, cmp)}</span>{" "}
        {example.question}
      </h3>
      <p className={styles.exampleGold}>
        <strong>Answer in the text:</strong> {example.gold_answer}
      </p>
      {example.models.map((m) => (
        <div key={m.id} className={styles.exampleModel}>
          <p className={styles.exampleModelName}>{m.label}</p>
          <div className={styles.rungGrid}>
            {RUNGS.map((r) => {
              const rung = m.rungs[r];
              if (!rung) return null;
              return (
                <section key={r} className={styles.rung}>
                  <header className={styles.rungHead}>
                    <span>{RUNG_LABEL[r]}</span>
                    <span
                      className={
                        rung.passed ? styles.rungPass : styles.rungFail
                      }
                    >
                      {rung.passed ? "passed" : "failed"}
                    </span>
                    {rung.citation && rung.citation !== "uncited" && (
                      <span
                        className={styles.rungCite}
                        title="Whether a chunk the answer cites holds the answer"
                      >
                        cites{" "}
                        {rung.citation === "correct"
                          ? "the right chunk"
                          : "the wrong chunk"}
                      </span>
                    )}
                  </header>
                  <pre className={styles.rungContext}>{rung.context}</pre>
                  <p className={styles.rungAnswer}>{rung.answer}</p>
                </section>
              );
            })}
          </div>
        </div>
      ))}
    </article>
  );
}

/** The section on a provisional board: the comparison is a claim, so none of it shows. */
function ComparisonWithheld() {
  return (
    <section className={styles.graphSection} aria-labelledby="graph-advantage">
      <h2 id="graph-advantage">Where the graph matters</h2>
      <p className={styles.provenance} role="note">
        The retrieval comparison (plain vector RAG, hybrid search, graph + vector on the same
        questions) is being re-run on the audited pipeline. It returns here
        with the record of what scored it; until then this board makes no
        claim about the graph.
      </p>
    </section>
  );
}

/** The corpus switch: one button per corpus, shown when the board reports more than one. */
function CorpusSwitch({
  corpora,
  selected,
  onSelect,
}: {
  corpora: CorpusComparison[];
  selected: string;
  onSelect: (id: string) => void;
}) {
  if (corpora.length < 2) return null;
  return (
    <div className={styles.corpusSwitch}>
      <span>Corpus:</span>
      <span
        className={styles.vramOptions}
        role="group"
        aria-label="Which corpus the comparison is about"
      >
        {corpora.map((c) => (
          <button
            type="button"
            key={c.id}
            aria-pressed={c.id === selected}
            onClick={() => onSelect(c.id)}
          >
            {c.label}
          </button>
        ))}
      </span>
      <span className={styles.corpusNote}>
        each corpus is measured and reported on its own; nothing is pooled
        across them
      </span>
    </div>
  );
}

/**
 * "Where the graph matters" for one corpus: the comparison table, how it was
 * made, and the worked examples, every number that corpus's own.
 */
function GraphAdvantage({
  shown,
  corpora,
  onSelect,
}: {
  shown: CorpusComparison;
  corpora: CorpusComparison[];
  onSelect: (id: string) => void;
}) {
  const { cmp, label, handGraded } = shown;
  const n = cmp.questions.length;
  const original = originalCount(cmp);
  return (
    <section className={styles.graphSection} aria-labelledby="graph-advantage">
      <h2 id="graph-advantage">Where the graph matters</h2>
      <CorpusSwitch corpora={corpora} selected={shown.id} onSelect={onSelect} />
      <p className={styles.lead}>
        On {label}: the same questions, the same prompt and the same chat
        model, three retrievals. Plain vector RAG is the tutorial pipeline (LlamaIndex, a sentence
        splitter, a vector index, the top four chunks). Hybrid search is
        ChaosCypher's own chunk search without the graph (keyword plus vector,
        the top ten). Graph + vector adds the graph: seed entities, PageRank, the
        chunks the entities cite merged with the same search, plus the entity
        and relationship lines. The {n} graph questions were written in advance
        to need what a graph provides, four kinds, and a question was dropped
        only when no rung could retrieve its answer, never because a baseline
        won it; every kept question counts under every rung. The lift is in
        questions, with a {INTERVAL} from a paired bootstrap over the
        questions.{" "}
        {original == null
          ? "The original questions are"
          : `The original ${original} are`}{" "}
        shown too, scored with every question in, so the lift on ordinary
        questions is visible beside it.
        {handGraded &&
          " Every answer on this corpus was also graded by hand; these are the hand-graded numbers (citation accuracy stays the scorer's check)."}
      </p>
      {cmp.value && <ValueBlock value={cmp.value} />}
      <div className={styles.tableWrap}>
        <ComparisonTable cmp={cmp} />
      </div>
      {cmp.examples.length > 0 && (
        <>
          <p className={styles.chatNote}>
            Worked examples: the graph questions with the largest lift over
            plain vector RAG, for the best local model and the best Claude row. Each
            rung's retrieved context is trimmed to the lines that hold an answer
            term and two lines around them; nothing is hand-edited.
          </p>
          {cmp.examples.map((e) => (
            <ExampleCard key={e.id} example={e} cmp={cmp} />
          ))}
        </>
      )}
    </section>
  );
}

const signed = (v: number | null) =>
  v == null ? "—" : `${v > 0 ? "+" : ""}${Math.round(v)}`;

/** What the graph is worth, four ways; a headline appears only when the exporter says its bar was met. */
function ValueBlock({ value }: { value: Value }) {
  const holding = value.headlines.filter((h) => headlineHolds(h, provenance));
  const band = value.lift.graph_advantage.naive;
  const ordinary = value.lift.original.naive;
  const removed =
    value.errors_removed.graph_advantage?.naive.median_pct ?? null;
  const best = value.size_substitution.best;
  return (
    <div className={styles.valueBlock}>
      {holding.length > 0 && (
        <ul className={styles.headlines}>
          {holding.map((h) => (
            <li key={h.key}>{h.text}</li>
          ))}
        </ul>
      )}
      <dl className={styles.valueGrid}>
        <div>
          <dt>Answered only with the graph</dt>
          <dd>
            {value.only_with_graph.count} of {value.only_with_graph.of} graph
            questions
            <small>
              no model got them right on plain vector RAG or hybrid search; at
              least half did on graph + vector
            </small>
          </dd>
        </div>
        <div>
          <dt>Lift, graph questions vs ordinary ones</dt>
          <dd>
            {signed(band.mean)} vs {signed(ordinary.mean)} points
            <small>
              pass rate, graph + vector minus plain vector RAG, mean over {value.models}{" "}
              models; the ordinary questions are the control, difference{" "}
              {signed(value.lift.differential.naive)}
            </small>
          </dd>
        </div>
        <div>
          <dt>Failed answers removed</dt>
          <dd>
            {removed == null ? "—" : `${Math.round(removed)}%`}
            <small>
              median over models on the graph questions, against plain vector RAG;{" "}
              {band.models_improved} of {band.models} models improved
            </small>
          </dd>
        </div>
        <div>
          <dt>Graph instead of size</dt>
          <dd>
            {best
              ? `${best.small_label} with the graph ≥ ${best.big_label} on plain vector RAG`
              : "no local pair yet"}
            <small>
              {best
                ? `${Math.round(best.small_graphrag_pct)}% vs ${Math.round(best.big_naive_pct)}% over every scored question, a ${best.ratio}× VRAM gap`
                : "needs two local models of different size with all three rungs"}
            </small>
          </dd>
        </div>
      </dl>
    </div>
  );
}

/** What each column means, one panel per header; the first line is its title. */
const HEADERS: Record<string, string[]> = {
  model: [
    "Model",
    "Hover a name for the Ollama tag, the license and any completion problems (a flag marks those).",
    "A row that ran through an MCP client says so there. VRAM and speed do not apply to it.",
  ],
  overall: [
    "Overall: one number for the whole job",
    `${share(WEIGHTS.overall.extraction)} of the extraction score plus ${share(WEIGHTS.overall.chat)} of the chat score, which is on ${BOARD_LABEL} only.`,
    "Needs both boards. A chat run that did not finish counts as 0, like any probe that does not finish.",
    `Every score carries a ${INTERVAL} from resampling its tasks, shown as ± beside the number. Sorted by Overall, ≈ marks a row within the interval of the row above: the board cannot tell those apart.`,
    "Speed is left out because it depends on the GPU.",
  ],
  extraction: [
    `Extraction: does the model follow the extraction prompt, ${THINKING}`,
    `In chunks, weighted ${share(WEIGHTS.extraction.carrier)}: each instruction inside ${CARRIER_TEXT}. This is what transfers to real data.`,
    `Isolated, weighted ${share(WEIGHTS.extraction.native)}: the same instructions alone in a short passage, the diagnostic behind the chunk score.`,
    "The strip under the score is the chunk run: one cell per probe, orange = failed. Hover a cell for its instruction.",
  ],
  chat: [
    "Chat: does it answer from the graph without inventing or giving up",
    `${chatCountLabel(chatSuite?.total ?? 80)} questions answered from a fixed reference graph of ${BOARD_LABEL} (built by Gemma 4 31B), ${CHAT_THINKING}.`,
    "Pass = names the right thing, invents nothing the retrieved context lacks, declines only when the sources really lack it.",
    chatExcluded > 0
      ? "Questions the fixture audit found unanswerable from the retrieved context are excluded from every row; they are the dashed cells in the strip."
      : "",
    'A model whose Ollama manifest has no tool calling is marked "no tools": it scores here but cannot be the app\'s chat model.',
  ].filter(Boolean),
  vram: [
    "VRAM: the size of the model weights",
    `Shown against your budget from the filter above, which keeps ${data.vram_headroom_gb} GB free for the context window.`,
  ],
  speed: [
    `Speed: median time per probe on one 32 GB consumer GPU, ${THINKING}`,
    "Fast under 15 s, moderate under 45 s, slow under 120 s, very slow above. Hover a cell for the seconds.",
  ],
};

/** The header's explanation, opened by hovering or focusing the header cell. */
function HeaderPanel({ id, align }: { id: string; align: "left" | "right" }) {
  return (
    <span
      className={`${styles.tipBox} ${align === "right" ? styles.tipRight : ""}`}
      role="tooltip"
    >
      {HEADERS[id].map((line, i) => (
        <span key={i}>{line}</span>
      ))}
    </span>
  );
}

function ModelTable({
  rows,
  sort,
  onSort,
  budget,
  detail,
}: {
  rows: Model[];
  sort: Sort;
  onSort: (key: SortKey) => void;
  budget: VramChoice;
  detail: boolean;
}) {
  const th = { sort, onSort };
  return (
    <table className={`${styles.table} ${detail ? styles.withDetail : ""}`}>
      <colgroup>
        <col className={styles.colModel} />
        <col className={styles.colOverall} />
        <col className={styles.colExtraction} />
        <col className={styles.colScore} />
        <col className={`${styles.colVram} ${styles.hidePhone}`} />
        <col className={`${styles.colSpeed} ${styles.hideMid}`} />
      </colgroup>
      <thead>
        <tr>
          <Th id="model" sortKey="model" {...th}>
            Model
          </Th>
          <Th id="overall" sortKey="overall" {...th}>
            Overall
          </Th>
          <Th id="extraction" sortKey="extraction" {...th}>
            Extraction
          </Th>
          <Th id="chat" sortKey="chat" {...th}>
            Chat
          </Th>
          <Th
            id="vram"
            sortKey="vram"
            className={styles.hidePhone}
            align="right"
            {...th}
          >
            VRAM
          </Th>
          <Th
            id="speed"
            sortKey="speed"
            className={styles.hideMid}
            align="right"
            {...th}
          >
            Speed
          </Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((m, i) => (
          <Fragment key={m.id}>
            <tr className={styles.main}>
              <ModelCell m={m} />
              <Overall
                m={m}
                tied={
                  sort.key === "overall" &&
                  i > 0 &&
                  overlaps(
                    interval(m.scores.overall_ci90),
                    interval(rows[i - 1].scores.overall_ci90),
                  )
                }
              />
              <Extraction m={m} />
              <ChatCell m={m} />
              <Vram m={m} budget={budget} />
              <Speed m={m} />
            </tr>
            {detail && <DetailRow m={m} />}
          </Fragment>
        ))}
      </tbody>
    </table>
  );
}

export default function Leaderboard() {
  const [vram, setVram] = useState<VramChoice>("any");
  const [sort, setSort] = useState<Sort>({ key: "overall", dir: "desc" });
  // The per-probe detail lines are off by default; the choice is remembered per browser.
  const [detail, setDetail] = useState<boolean>(() => {
    try {
      return (
        typeof window !== "undefined" &&
        window.localStorage.getItem("leaderboard.detail") === "1"
      );
    } catch {
      return false;
    }
  });
  const toggleDetail = () => {
    const next = !detail;
    setDetail(next);
    try {
      window.localStorage.setItem("leaderboard.detail", next ? "1" : "0");
    } catch {
      /* private mode or storage blocked: the toggle still works for this visit */
    }
  };
  const onSort = (key: SortKey) =>
    setSort((s) =>
      s.key === key
        ? { key, dir: s.dir === "asc" ? "desc" : "asc" }
        : { key, dir: FIRST_DIR[key] },
    );

  const rows = useMemo(() => {
    // A harness row runs in the client, not on your GPU, so the VRAM filter lets it through.
    const fit =
      vram === "any"
        ? models
        : models.filter(
            (m) =>
              m.harness != null ||
              (m.vram_gb != null && m.vram_gb <= vram - data.vram_headroom_gb),
          );
    return sortRows(fit, sort);
  }, [vram, sort]);
  const harnessCount = models.filter((m) => m.harness != null).length;
  // Which corpus "Where the graph matters" shows: the board corpus first.
  const [corpusId, setCorpusId] = useState<string | undefined>(comparisons[0]?.id);
  const shown = comparisons.find((c) => c.id === corpusId) ?? comparisons[0];

  const nativeTotal = data.suites.native.total;
  const carrierTotal = data.suites.carrier.total;
  const chatTotal = chatSuite?.total ?? 80;

  return (
    <Layout
      title="Model leaderboard"
      description="Which local models follow the ChaosCypher extraction prompt and answer grounded questions from a knowledge graph, measured with pass/fail probes through the real pipeline."
    >
      <main className={styles.page}>
        <h1>Model leaderboard</h1>
        <p className={styles.lead}>
          Two jobs, measured through the real ChaosCypher pipeline. Extraction:
          does the model follow the extraction prompt, on {nativeTotal}{" "}
          pass/fail instruction probes and then the same instructions inside
          production-sized chunks ({carrierTotal} probes), {THINKING}.
          Grounded chat: does it answer from a fixed reference graph of{" "}
          {BOARD_LABEL} without inventing or giving up, on{" "}
          {chatCountLabel(chatTotal)} questions, {CHAT_THINKING}. Overall is {share(WEIGHTS.overall.extraction)} extraction
          (in chunks counted twice, isolated once) and{" "}
          {share(WEIGHTS.overall.chat)} grounded chat; hover any score or column
          name for what is behind it. Temperature 0, seed 42; a probe that does
          not finish counts as a fail. Last run {data.last_run}.{" "}
          <Link to="/docs/reference/extraction-benchmark">
            How it is scored
          </Link>
          .
        </p>

        {provenance.provisional && (
          <p className={styles.provenance} role="note">
            {provenanceNote(provenance)}
          </p>
        )}

        <div className={styles.controls}>
          <fieldset className={styles.vram}>
            <legend>
              I have<span className={styles.phoneOnly}>, in GB of VRAM,</span>
            </legend>
            <span
              className={styles.vramOptions}
              role="group"
              aria-label="VRAM available"
            >
              {[...VRAM_STEPS, "any" as const].map((step) => (
                <button
                  type="button"
                  key={step}
                  aria-pressed={vram === step}
                  onClick={() => setVram(step)}
                >
                  {step === "any" ? "any" : `${step} GB`}
                </button>
              ))}
            </span>
            <span className={styles.hidePhone}>of VRAM</span>
          </fieldset>
          <span className={styles.count}>
            {rows.length} of {models.length} models. Click a column to sort.
          </span>
          <button
            type="button"
            className={`${styles.detailToggle} ${styles.hideMid}`}
            aria-pressed={detail}
            onClick={toggleDetail}
            title="One cell per probe and per question under each model, in the same order on every row: orange = failed, dim = a retrieval miss every model shares. Hover a cell for the instruction or question."
          >
            {detail ? "Hide" : "Show"} every probe
          </button>
        </div>

        <div className={styles.tableWrap}>
          <ModelTable
            rows={rows}
            sort={sort}
            onSort={onSort}
            budget={vram}
            detail={detail}
          />
          {rows.length === 0 && (
            <p className={styles.empty}>
              No measured model fits in {vram} GB with {data.vram_headroom_gb}{" "}
              GB left for context. Pick a larger budget.
            </p>
          )}
        </div>

        {chatSuite &&
          (chatExcluded > 0 || (chatSuite.retrieval_floor ?? 0) > 0) && (
            <p className={styles.chatNote}>
              Grounded chat:{" "}
              {chatExcluded > 0 &&
                `${chatExcluded} of ${chatAsked} questions are excluded by the fixture audit (the retrieved context on this graph cannot answer them), so every model is scored on ${chatSuite.total}. `}
              {(chatSuite.retrieval_floor ?? 0) > 0 &&
                `${chatSuite.retrieval_floor} more ${chatSuite.retrieval_floor === 1 ? "question was" : "questions were"} failed by every model (retrieval floor), so the effective ceiling is ${chatSuite.total - (chatSuite.retrieval_floor ?? 0)}.`}
            </p>
          )}

        {shown &&
          (comparisonShows(provenance) ? (
            <GraphAdvantage
              shown={shown}
              corpora={comparisons}
              onSelect={setCorpusId}
            />
          ) : (
            <ComparisonWithheld />
          ))}

        {harnessCount > 0 && (
          <p className={styles.chatNote}>
            A row named "via" a client ran through an MCP client on the user's
            own subscription (Claude Code here), not through Ollama. Same
            probes, parser and checks, but the client's own temperature, seed,
            thinking and system prompt instead of our pins, and the model name
            is what the client reported. So what the model did with our prompt
            is comparable; why it did it (the model or the harness around it) is
            not. VRAM and speed show "—" on those rows; chat and Overall appear
            once the client has answered the chat suite too.
          </p>
        )}

        <p className={styles.repro}>
          The extraction columns come from <code>chaoscypher benchmark run probes --local-only</code> and the
          chat column from <code>chaoscypher benchmark run chat-probes --local-only</code>, on consumer GPUs
          with the models pulled from Ollama (each model&apos;s rows on one card); the scorer and the probes are in the repository. Hover a cell in the strip to read the instruction that failed.
        </p>
      </main>
    </Layout>
  );
}
