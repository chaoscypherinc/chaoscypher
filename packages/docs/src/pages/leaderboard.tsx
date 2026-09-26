import { Fragment, useMemo, useState } from "react";
import type React from "react";
import Layout from "@theme/Layout";
import Link from "@docusaurus/Link";
import data from "../data/leaderboard.json";
import styles from "./leaderboard.module.css";
import { speedFill, speedLabel } from "../lib/leaderboardScore";

type Suite = {
  passed: number;
  total: number;
  p50_ms: number;
  minutes: number;
  truncated: number;
  aborted: number;
  thinking_honoured: boolean | null;
  verdicts: Record<string, boolean>;
};

/** Grounded chat on the fixed reference graph; passed/pct are null when the run did not finish. */
type Chat = {
  passed: number | null;
  total: number | null;
  pct: number | null;
  minutes: number;
  truncated: number;
  timed_out: boolean;
  /** Null on a harness row: the client ran it on its own thinking setting. */
  thinking: boolean | null;
  unsupported_names: number | null;
  /** One verdict per question; empty when the run did not finish. */
  verdicts?: Record<string, boolean>;
};

type ChatQuestion = { id: string; section: string; tier: string; question: string };
type ChatSuite = {
  total: number;
  fixture: string;
  retrieval_floor: number | null;
  retrieval_floor_ids?: string[];
  questions?: ChatQuestion[];
};

/** The blended percentages the exporter computes; null where a board was not run. */
type Scores = { extraction: number | null; chat: number | null; overall: number | null };

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
// Absent until an export includes a chat run.
const chatSuite = (data.suites as { chat?: ChatSuite }).chat;
const chatQuestions: ChatQuestion[] = chatSuite?.questions ?? [];
const floorIds = new Set(chatSuite?.retrieval_floor_ids ?? []);
const CHAT_SECTION_LABEL: Record<string, string> = {
  factual_single_hop: "single-hop",
  paraphrase: "paraphrase",
  multi_hop: "multi-hop",
  fine_grained_discrimination: "fine-grained",
  out_of_scope: "out of scope",
};
// How the exporter blended the scores on every row (see scripts/benchmark/export_leaderboard.py).
const WEIGHTS = data.score_weights as { extraction: { carrier: number; native: number }; overall: { extraction: number; chat: number } };
const share = (w: number) => `${Math.round(100 * w)}%`;

function rate(s?: Suite): number {
  return s && s.total ? s.passed / s.total : -1;
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
  const c = typeof va === "string" ? va.localeCompare(vb as string) : va - (vb as number);
  return dir === "asc" ? c : -c;
}

function sortRows(rows: Model[], sort: Sort): Model[] {
  return [...rows].sort((a, b) => compare(a, b, sort) || rate(b.native) - rate(a.native));
}

const pct = (v: number) => `${Math.round(v)}%`;
const counts = (c?: { passed: number | null; total: number | null }) => (c ? `${c.passed ?? 0}/${c.total}` : "—");

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
      <span className={`${styles.tipBox} ${align === "right" ? styles.tipRight : ""}`} role="tooltip">
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
      aria-sort={active ? (sort?.dir === "asc" ? "ascending" : "descending") : "none"}
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

/** The blended score with its arithmetic in the hover; "—" until every board has run. */
function Overall({ m }: { m: Model }) {
  const value = m.scores.overall;
  if (value == null) {
    return (
      <td className={styles.score} title="Needs the extraction probes and the grounded-chat board.">
        —
      </td>
    );
  }
  const e = m.scores.extraction!;
  const c = m.scores.chat!;
  const lines = [
    `Overall ${pct(value)} = ${share(WEIGHTS.overall.extraction)} of extraction + ${share(WEIGHTS.overall.chat)} of chat`,
    `Extraction ${pct(e)}: ${WEIGHTS.overall.extraction} × ${pct(e)} = ${(WEIGHTS.overall.extraction * e).toFixed(1)} points`,
    `Chat ${pct(c)}: ${WEIGHTS.overall.chat} × ${pct(c)} = ${(WEIGHTS.overall.chat * c).toFixed(1)} points${m.chat?.timed_out ? " (the run did not finish, so chat counts as 0)" : ""}`,
    "Speed is not part of it: it depends on the GPU.",
  ];
  return (
    <td className={styles.score}>
      <Tip label={<span className={`${styles.chip} ${styles.chipOverall}`}>{pct(value)}</span>} lines={lines} />
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
      <td className={styles.score} title="Needs both probe boards: isolated and in chunks.">
        —
      </td>
    );
  }
  const lines = [
    `Extraction ${pct(value)}: does the model follow the extraction prompt, thinking off`,
    `In chunks ${counts(m.carrier)}, ${pct(100 * rate(m.carrier))}: each instruction inside a real 3,000-character extraction group with 21 named people. Weighted ${share(WEIGHTS.extraction.carrier)}, because this is what transfers to real data.`,
    `Isolated ${counts(m.native)}, ${pct(100 * rate(m.native))}: the same instructions alone in a short passage. Weighted ${share(WEIGHTS.extraction.native)}, as the diagnostic behind the chunk score.`,
    "The strips under the row show every probe: one cell each, orange = failed. Hover a cell for its instruction.",
  ];
  return (
    <td className={styles.score}>
      <Tip label={<span className={styles.chip}>{pct(value)}</span>} lines={lines} />
      <span className={styles.scoreBar} aria-hidden="true">
        <i style={{ width: `${Math.round(value)}%` }} />
      </span>
    </td>
  );
}

/** One cell per probe or question, grouped by section; the same column is the same item on every row. */
function Strip({
  label,
  groups,
}: {
  label: string;
  groups: { key: string; cells: { id: string; ok: boolean | null; floor?: boolean; title: string }[] }[];
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
              <span
                key={c.id}
                className={`${styles.cell} ${c.ok === false ? styles.fail : ""} ${c.floor ? styles.floor : ""} ${c.ok == null ? styles.unknown : ""}`}
                title={c.title}
              />
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
            .map((p) => {
              const ok = suite.verdicts[p.id];
              return { id: p.id, ok, title: `${p.id} — ${ok ? "pass" : "fail"}: ${p.instruction}` };
            })
        : [],
    }));
  const chatSections = [...new Set(chatQuestions.map((q) => q.section))];
  const v = m.chat?.verdicts ?? {};
  const scored = m.chat != null && m.chat.passed != null;
  const chatGroups = chatSections.map((sec) => ({
    key: sec,
    cells: !scored
      ? []
      : chatQuestions
          .filter((q) => q.section === sec)
          .map((q) => {
            const ok = q.id in v ? v[q.id] : null;
            const floor = floorIds.has(q.id);
            const state = ok == null ? "not scored" : ok ? "pass" : floor ? "fail (retrieval miss: every model failed it)" : "fail";
            return {
              id: q.id,
              ok,
              floor,
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
      <td className={styles.score} title={m.harness ? "Not measured: the client runs the model." : "Not measured."}>
        —
      </td>
    );
  }
  const flags: string[] = [];
  const noTools = m.tools === false;
  if (noTools) flags.push("no tool calling: the app's chat loop needs it, so this model cannot be the chat model");
  if (c.timed_out) flags.push("did not finish: the run hit the per-model time limit");
  if (c.truncated > 0) flags.push(`${c.truncated} ${c.truncated === 1 ? "answer" : "answers"} hit the output-token cap`);
  const detail = [
    c.passed != null
      ? `Chat ${pct(m.scores.chat!)}: ${c.passed}/${c.total} questions answered right from a fixed reference graph of War and Peace Book One, ${m.harness ? "on the client's own settings" : "thinking on"}`
      : "Chat: the run did not finish, so it counts as 0",
    "Pass = names the right thing, invents nothing the retrieved context lacks, declines only when the sources really lack it.",
    chatSuite?.retrieval_floor != null
      ? `${chatSuite.retrieval_floor} of ${chatSuite.total} questions are retrieval misses on this graph (the fact never reached any model's context), so the ceiling is ${chatSuite.total - chatSuite.retrieval_floor}.`
      : "",
    c.passed != null && !m.harness ? `${Math.round(c.minutes)} min for the run.` : "",
    ...flags.map((f) => f[0].toUpperCase() + f.slice(1) + "."),
  ].filter(Boolean);
  const tip = { role: "img", "aria-label": flags.join("; "), title: flags.join("\n") };
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
        <Tip label={<span className={styles.dnf}>did not finish</span>} lines={detail} /> {flag}
      </td>
    );
  }
  const value = m.scores.chat!;
  return (
    <td className={styles.score}>
      <Tip label={<span className={styles.chip}>{pct(value)}</span>} lines={detail} /> {flag}
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
      <td className={`${styles.num} ${styles.hidePhone}`} title={m.harness ? "Runs in the client, not on your GPU." : "Not known."}>
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
      <Tip label={<span className={styles.gaugeLabel}>{m.vram_gb} GB</span>} lines={lines} align="right" />
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
      <td className={`${styles.num} ${styles.hideMid}`} title={m.harness ? "Not measured: the client runs the model." : "Not measured."}>
        —
      </td>
    );
  }
  const label = speedLabel(ms);
  const run = m.carrier ? `the ${m.carrier.total}-probe chunk run took ${Math.round(m.carrier.minutes)} min` : "";
  const lines = [
    `${label}: ${(ms / 1000).toFixed(1)} s median per probe on one RTX 5090, thinking off${run ? `; ${run}` : ""}.`,
    "One probe is a production-sized chunk group of about 3,000 characters.",
    "Fast is under 15 s, moderate under 45 s, slow under 120 s, very slow above. Fixed thresholds, so a label never moves when other models are added.",
  ];
  return (
    <td className={`${styles.gaugeCell} ${styles.hideMid}`}>
      <Tip label={<span className={styles.gaugeLabel}>{label}</span>} lines={lines} align="right" />
      <span className={`${styles.gauge} ${label === "Very slow" ? styles.gaugeLow : ""}`} aria-hidden="true">
        <i style={{ width: `${Math.round(100 * speedFill(ms))}%` }} />
      </span>
    </td>
  );
}

/** What each column means, one panel per header; the first line is its title. */
const HEADERS: Record<string, string[]> = {
  model: [
    "Model",
    "Hover a name for the Ollama tag, the license and any completion problems (a flag marks those).",
    "A row that ran through an MCP client says so there. VRAM, speed and chat do not apply to it.",
  ],
  overall: [
    "Overall: one number for the whole job",
    `${share(WEIGHTS.overall.extraction)} of the extraction score plus ${share(WEIGHTS.overall.chat)} of the chat score.`,
    "Needs both boards. A chat run that did not finish counts as 0, like any probe that does not finish.",
    "Speed is left out because it depends on the GPU.",
  ],
  extraction: [
    "Extraction: does the model follow the extraction prompt, thinking off",
    `In chunks, weighted ${share(WEIGHTS.extraction.carrier)}: each instruction inside a real 3,000-character extraction group with 21 named people. This is what transfers to real data.`,
    `Isolated, weighted ${share(WEIGHTS.extraction.native)}: the same instructions alone in a short passage, the diagnostic behind the chunk score.`,
    "The strip under the score is the chunk run: one cell per probe, orange = failed. Hover a cell for its instruction.",
  ],
  chat: [
    "Chat: does it answer from the graph without inventing or giving up",
    `${chatSuite?.total ?? 80} questions answered from a fixed reference graph of War and Peace Book One (built by Gemma 4 31B), thinking on.`,
    "Pass = names the right thing, invents nothing the retrieved context lacks, declines only when the sources really lack it.",
    "A model whose Ollama manifest has no tool calling is marked \"no tools\": it scores here but cannot be the app's chat model.",
  ],
  vram: [
    "VRAM: the size of the model weights",
    `Shown against your budget from the filter above, which keeps ${data.vram_headroom_gb} GB free for the context window.`,
  ],
  speed: [
    "Speed: median time per probe on one RTX 5090, thinking off",
    "Fast under 15 s, moderate under 45 s, slow under 120 s, very slow above. Hover a cell for the seconds.",
  ],
};

/** The header's explanation, opened by hovering or focusing the header cell. */
function HeaderPanel({ id, align }: { id: string; align: "left" | "right" }) {
  return (
    <span className={`${styles.tipBox} ${align === "right" ? styles.tipRight : ""}`} role="tooltip">
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
          <Th id="model" sortKey="model" {...th}>Model</Th>
          <Th id="overall" sortKey="overall" {...th}>Overall</Th>
          <Th id="extraction" sortKey="extraction" {...th}>Extraction</Th>
          <Th id="chat" sortKey="chat" {...th}>Chat</Th>
          <Th id="vram" sortKey="vram" className={styles.hidePhone} align="right" {...th}>VRAM</Th>
          <Th id="speed" sortKey="speed" className={styles.hideMid} align="right" {...th}>Speed</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((m) => (
          <Fragment key={m.id}>
            <tr className={styles.main}>
              <ModelCell m={m} />
              <Overall m={m} />
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
      return typeof window !== "undefined" && window.localStorage.getItem("leaderboard.detail") === "1";
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
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: FIRST_DIR[key] }));

  const rows = useMemo(() => {
    // A harness row runs in the client, not on your GPU, so the VRAM filter lets it through.
    const fit =
      vram === "any"
        ? models
        : models.filter((m) => m.harness != null || (m.vram_gb != null && m.vram_gb <= vram - data.vram_headroom_gb));
    return sortRows(fit, sort);
  }, [vram, sort]);
  const harnessCount = models.filter((m) => m.harness != null).length;

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
          Two jobs, measured through the real ChaosCypher pipeline. Extraction: does the model follow the
          extraction prompt, on {nativeTotal} pass/fail instruction probes and then the same instructions
          inside production-sized chunks ({carrierTotal} probes), thinking off. Grounded chat: does it answer
          from a fixed reference graph without inventing or giving up, on {chatTotal} questions, thinking on.
          Overall is {share(WEIGHTS.overall.extraction)} extraction (in chunks counted twice, isolated once) and{" "}
          {share(WEIGHTS.overall.chat)} grounded chat; hover any score or column name for what is behind it.
          Temperature 0, seed 42; a probe that does not finish counts as a fail. Last run {data.generated}.{" "}
          <Link to="/docs/reference/extraction-benchmark">How it is scored</Link>.
        </p>

        <div className={styles.controls}>
          <fieldset className={styles.vram}>
            <legend>
              I have<span className={styles.phoneOnly}>, in GB of VRAM,</span>
            </legend>
            <span className={styles.vramOptions} role="group" aria-label="VRAM available">
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
          <ModelTable rows={rows} sort={sort} onSort={onSort} budget={vram} detail={detail} />
          {rows.length === 0 && (
            <p className={styles.empty}>
              No measured model fits in {vram} GB with {data.vram_headroom_gb} GB left for context. Pick a larger budget.
            </p>
          )}
        </div>

        {chatSuite?.retrieval_floor != null && (
          <p className={styles.chatNote}>
            Grounded chat: {chatSuite.retrieval_floor} of {chatSuite.total} questions are retrieval misses on this
            graph (the fact was not in any model's retrieved context), so the effective ceiling is{" "}
            {chatSuite.total - chatSuite.retrieval_floor}.
          </p>
        )}

        {harnessCount > 0 && (
          <p className={styles.chatNote}>
            A row named "via" a client ran through an MCP client on the user's own subscription (Claude Code
            here), not through Ollama. Same probes, parser and checks, but the client's own temperature, seed,
            thinking and system prompt instead of our pins, and the model name is what the client reported.
            So what the model did with our prompt is comparable; why it did it (the model or the harness
            around it) is not. VRAM and speed show "—" on those rows; chat and Overall appear once the client has
            answered the chat suite too.
          </p>
        )}

        <p className={styles.repro}>
          Every number here comes from <code>chaoscypher benchmark run probes --local-only</code> on a
          single RTX 5090 with the models pulled from Ollama; the scorer, the probes and the raw JSON are in
          the repository. Hover a cell in the strip to read the instruction that failed.
        </p>
      </main>
    </Layout>
  );
}
