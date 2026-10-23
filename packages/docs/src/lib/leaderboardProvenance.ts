/**
 * What the board's `provenance` block says, read once for the leaderboard page
 * and the homepage teaser (audit wave 5).
 *
 * The exporter writes the block (scripts/benchmark/export_leaderboard.py
 * `_provenance`); `provenance_problems` there is the same rule in Python.
 * `provenanceProblems` below mirrors it check for check (audit wave 6: this
 * page skipped the findings-without-override, unknown-flag and drop checks),
 * and a parity test (packages/cli/tests/unit/benchmark/
 * test_export_leaderboard_script.py) runs both on the same boards and pins
 * the key and flag lists below to the exporter's. Unknown is never clean: a
 * board without the block, or with an unrecorded one (every key null, an
 * export from before the block), is provisional, and so is a board an
 * override flag let through, and so is a board in a layout other than the
 * exporter's (`BOARD_FORMAT`, older ones included). On a provisional board no
 * headline holds.
 */
import { BOARD_FORMAT, boardFormat, isRecord } from "./boardShape";

/** The block's keys, in the exporter's order (`PROVENANCE_KEYS` in the exporter). */
export const PROVENANCE_KEYS = [
  "overrides",
  "findings",
  "dropped_models",
  "scorer_version",
  "scorer_recipe",
  "fixture_sha256",
] as const;

/** The flags that let a board through a refusal (`OVERRIDE_FLAGS` in the exporter). */
export const OVERRIDE_FLAGS = [
  "--allow-unpinned",
  "--allow-mixed",
  "--allow-oracle",
  "--include-errored",
  "--drop-errored-models",
  "--drop-timed-out-models",
] as const;

/** What a board with a chat suite must record (`CHAT_PROVENANCE_KEYS` in the exporter). */
export const CHAT_PROVENANCE_KEYS = [
  "scorer_version",
  "scorer_recipe",
  "fixture_sha256",
] as const;

export type DroppedRow = {
  model: string;
  why: string;
  /** The override that dropped it; null for a row left out without one (e.g. judged). */
  flag: string | null;
};

export type Provenance = {
  overrides: string[] | null;
  findings: string[] | null;
  dropped_models: DroppedRow[] | null;
  scorer_version: number | null;
  scorer_recipe: string | null;
  fixture_sha256: string | null;
};

export type BoardProvenance = {
  /** The block exists, is recorded, and is sound (`provenanceProblems` is empty). */
  known: boolean;
  /** Unknown, or published with an override: the page shows a banner, no headline holds. */
  provisional: boolean;
  overrides: string[];
  findings: string[];
  dropped: DroppedRow[];
};

/** Python's truthiness, which the exporter's rule reads by: null, false, 0, "", [] and {} are false. */
function truthy(value: unknown): boolean {
  if (Array.isArray(value)) return value.length > 0;
  if (value !== null && typeof value === "object") return Object.keys(value).length > 0;
  return !!value;
}

/** Why a board's provenance block is missing, unrecorded or not self-consistent; empty when sound. */
export function provenanceProblems(board: unknown): string[] {
  const b = isRecord(board) ? board : {};
  if (boardFormat(b) !== BOARD_FORMAT) {
    const named = JSON.stringify(b.board_format === undefined ? 1 : b.board_format);
    return [`board format ${named}: the page reads format ${BOARD_FORMAT} only`];
  }
  const block = b.provenance;
  if (!isRecord(block)) {
    return ["no provenance block: an export from before audit wave 4, scored by unknown code"];
  }
  const missing = PROVENANCE_KEYS.filter((k) => !(k in block));
  if (missing.length > 0) return [`the provenance block lacks ${missing.join(", ")}`];
  const overrides = block.overrides;
  if (overrides === null) {
    const stray = PROVENANCE_KEYS.filter((k) => block[k] !== null);
    if (stray.length > 0) {
      return [`an unrecorded provenance block (overrides null) records ${stray.join(", ")}`];
    }
    return ["the provenance block is unrecorded (every key null)"];
  }
  const notLists = (["overrides", "findings", "dropped_models"] as const).filter(
    (k) => !Array.isArray(block[k]),
  );
  if (notLists.length > 0) {
    return [`a recorded provenance block whose ${notLists.join(", ")} is not a list`];
  }
  const passed = overrides as unknown[];
  const problems: string[] = [];
  const known: readonly string[] = OVERRIDE_FLAGS;
  const unknown = passed.filter((f) => typeof f !== "string" || !known.includes(f));
  if (unknown.length > 0) {
    problems.push(`overrides name flags the exporter has not got: ${unknown.join(", ")}`);
  }
  if (truthy(block.findings) && passed.length === 0) {
    problems.push("findings were lifted but no override is recorded");
  }
  for (const drop of block.dropped_models as unknown[]) {
    if (!isRecord(drop)) {
      problems.push(`a dropped row that is not a record: ${JSON.stringify(drop)}`);
    } else if (drop.flag != null && !passed.includes(drop.flag)) {
      problems.push(`${String(drop.model)} was dropped by ${String(drop.flag)}, not passed`);
    }
  }
  const suites = isRecord(b.suites) ? b.suites : {};
  if (truthy(suites.chat)) {
    for (const key of CHAT_PROVENANCE_KEYS) {
      if (block[key] == null || block[key] === "") problems.push(`a chat board without ${key}`);
    }
  }
  return problems;
}

/** Read a board's provenance; anything missing or inconsistent reads as unknown. */
export function readProvenance(board: unknown): BoardProvenance {
  const p = isRecord(board) && isRecord(board.provenance) ? board.provenance : null;
  const known = provenanceProblems(board).length === 0;
  const overrides = p && Array.isArray(p.overrides) ? (p.overrides as string[]) : [];
  return {
    known,
    provisional: !known || overrides.length > 0,
    overrides,
    findings: p && Array.isArray(p.findings) ? (p.findings as string[]) : [],
    dropped: p && Array.isArray(p.dropped_models) ? (p.dropped_models as DroppedRow[]) : [],
  };
}

/**
 * Whether the retrieval comparison may be shown at all: headlines, the value
 * grid, the comparison table, the worked examples and the homepage's one line.
 * A provisional board makes no claim (2026-10-19: the page withheld the
 * headlines but still drew the grid and table from the same rows).
 */
export function comparisonShows(provenance: BoardProvenance): boolean {
  return !provenance.provisional;
}

/** Whether a headline may be shown: its bar is met and the comparison shows. */
export function headlineHolds(
  h: { holds: boolean; text: string },
  provenance: BoardProvenance,
): boolean {
  return h.holds && !!h.text && comparisonShows(provenance);
}
