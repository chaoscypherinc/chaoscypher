/**
 * The homepage hero's benchmark figures, read from the exported board, one
 * section per corpus.
 *
 * Every figure is one of the exporter's comparison headlines
 * (scripts/benchmark/export_leaderboard.py `_value`), shown only when that
 * headline holds on a board that is not provisional (`headlineHolds`), so the
 * hero can never state a number the leaderboard page does not. A board with
 * no holding headline shows no figures, and the hero falls back to its copy.
 *
 * The order follows the 2026-10-21 hero research: the outcome (fewer failed
 * answers on ordinary questions), the efficiency (a smaller model, the same
 * answers), then the mechanism (questions that connect facts). Each label
 * names its baseline and each detail line gives the model count and the
 * absolute figures behind the relative one.
 *
 * Each corpus is reported on its own (2026-10-22, `boardCorpora`): a section
 * holds one corpus's holding headlines and names that corpus, and a corpus
 * where none holds is not shown. Nothing is pooled across corpora.
 */
import { boardCorpora } from "./boardCorpora";
import { headlineHolds, readProvenance } from "./leaderboardProvenance";

export type HeroStat = {
  key: string;
  /** The big figure: "33%", "3.7×". */
  figure: string;
  /** What the figure means, baseline included. */
  label: string;
  /** The small print: which models, the absolute numbers. */
  detail: string;
};

export type HeroMethod = {
  models: number;
  questions: number;
};

/** One corpus's figures: its id and label, its holding headlines, and how they were measured. */
export type HeroSection = {
  corpus: string;
  label: string;
  /**
   * "On {label}" above the figures, or null when the method line names the
   * corpus already: every figure is named by one or the other.
   */
  heading: string | null;
  stats: HeroStat[];
  method: HeroMethod | null;
};

type Headline = {
  key: string;
  holds: boolean;
  text: string;
  values: Record<string, unknown>;
};

/** The hero shows these headlines, in this order, when they hold. */
export const HERO_KEYS = ["failures_ordinary", "size_substitution", "errors_removed"] as const;

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const pct = (v: number) => `${Math.round(v)}%`;

/** "all 22" when every model, else "19 of 22". */
function share(part: number, whole: number): string {
  return part === whole ? `all ${whole}` : `${part} of ${whole}`;
}

function failuresOrdinary(v: Record<string, unknown>): HeroStat | null {
  const median = num(v.median_pct);
  const fewer = num(v.models_fewer);
  const models = num(v.models);
  const before = num(v.fail_pct_baseline);
  const after = num(v.fail_pct_graphrag);
  if (median == null || fewer == null || models == null || before == null || after == null) {
    return null;
  }
  return {
    key: "failures_ordinary",
    figure: pct(median),
    label: "fewer failed answers than plain vector RAG",
    detail: `Fewer on ${share(fewer, models)} local models tested · failed answers ${pct(before)} → ${pct(after)}`,
  };
}

function sizeSubstitution(v: Record<string, unknown>): HeroStat | null {
  const ratio = num(v.ratio);
  const small = str(v.small_label);
  const big = str(v.big_label);
  const smallPct = num(v.small_graphrag_pct);
  const bigPct = num(v.big_naive_pct);
  const matching = num(v.models_matching);
  if (ratio == null || !small || !big || smallPct == null || bigPct == null || matching == null) {
    return null;
  }
  const verb = smallPct > bigPct ? "beats" : "matches";
  return {
    key: "size_substitution",
    figure: `${ratio}×`,
    label: "less GPU memory for the same answers",
    detail: `${small} with the graph ${verb} ${big} on plain vector RAG (${pct(smallPct)} vs ${pct(bigPct)})`,
  };
}

function errorsRemoved(v: Record<string, unknown>): HeroStat | null {
  const median = num(v.median_pct);
  const models = num(v.models);
  if (median == null || models == null) return null;
  return {
    key: "errors_removed",
    figure: pct(median),
    label: "fewer failed answers on questions that connect facts",
    detail: `Median over ${models} local models, against plain vector RAG`,
  };
}

const BUILDERS: Record<(typeof HERO_KEYS)[number], (v: Record<string, unknown>) => HeroStat | null> = {
  failures_ordinary: failuresOrdinary,
  size_substitution: sizeSubstitution,
  errors_removed: errorsRemoved,
};

/** One corpus's comparison block. */
type Comparison = {
  value?: { models?: unknown; headlines?: Headline[] };
  questions?: unknown[];
};

/** The hero's figures for one corpus's comparison: the holding headlines of HERO_KEYS, in order. */
function statsOf(cmp: Comparison, provenance: ReturnType<typeof readProvenance>): HeroStat[] {
  const headlines = cmp.value?.headlines ?? [];
  const out: HeroStat[] = [];
  for (const key of HERO_KEYS) {
    const h = headlines.find((x) => x.key === key);
    if (!h || !headlineHolds(h, provenance)) continue;
    const stat = BUILDERS[key](h.values ?? {});
    if (stat) out.push(stat);
  }
  return out;
}

/** What one corpus's methodology line counts: the models compared and the questions they were asked. */
function methodOf(cmp: Comparison): HeroMethod | null {
  const models = num(cmp.value?.models);
  const questions = Array.isArray(cmp.questions) ? cmp.questions.length : null;
  const ordinary = (cmp.value?.headlines ?? []).find((h) => h.key === "failures_ordinary");
  const asked = num(ordinary?.values?.questions);
  if (models == null || questions == null || asked == null) return null;
  return { models, questions: questions + asked };
}

/** The hero's sections for ``board``: one per corpus with a holding headline, in report order. */
export function heroSections(board: unknown): HeroSection[] {
  const provenance = readProvenance(board);
  const out: Omit<HeroSection, "heading">[] = [];
  for (const corpus of boardCorpora<{ comparison?: Comparison }>(board)) {
    const cmp = corpus.suite?.comparison;
    if (!cmp || typeof cmp !== "object") continue;
    const stats = statsOf(cmp, provenance);
    if (stats.length === 0) continue;
    out.push({ corpus: corpus.id, label: corpus.label, stats, method: methodOf(cmp) });
  }
  // several corpora: each is headed; one: its method line names it, else a heading does
  return out.map((s) => ({
    ...s,
    heading: out.length > 1 || s.method == null ? `On ${s.label}` : null,
  }));
}
