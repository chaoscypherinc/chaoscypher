/**
 * The shape of an exported board, read one way by every module of the page.
 *
 * The exporter (scripts/benchmark/export_leaderboard.py `BOARD_FORMAT`) writes
 * one layout; a board in any other layout, older ones included, is not read:
 * it is provisional (`provenanceProblems`) and has no corpora
 * (`boardCorpora`), so the page makes no claim from it.
 */

/** The one layout the page reads (`BOARD_FORMAT` in the exporter). */
export const BOARD_FORMAT = 2;

/** A plain JSON object (not null, not an array). */
export function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** The board's layout as it names it, or null when it names none or not a whole number. */
export function boardFormat(board: unknown): number | null {
  const fmt = isRecord(board) ? board.board_format : undefined;
  return typeof fmt === "number" && Number.isInteger(fmt) ? fmt : null;
}
