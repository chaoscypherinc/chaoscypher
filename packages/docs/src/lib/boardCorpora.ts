/**
 * The corpora a board reports, each on its own (2026-10-22).
 *
 * Every headline, bar, interval and count the page and the homepage show is
 * one corpus's, and each says which corpus it is about; nothing is pooled
 * across corpora. The exporter (scripts/benchmark/export_leaderboard.py)
 * lists every corpus in `corpora` (`id`, `label`, `board`); the board
 * corpus's suite is `suites.chat` (the table's chat column and Overall rest
 * on it) and every other corpus's entry carries its own `chat`.
 *
 * A board in another layout (`BOARD_FORMAT`), or one that breaks the
 * exporter's layout rules (`_corpora_problems`: exactly one board corpus, the
 * chat suite's, and no corpus twice), reads as no corpora: it makes no claim.
 *
 * Where a full hand review of a corpus's comparison answers exists, the
 * exporter computes that corpus's comparison on the reviewers' verdicts
 * (2026-10-23): `handGraded` says so, and the page and the hero say it too.
 */
import { BOARD_FORMAT, boardFormat, isRecord } from "./boardShape";

/** One corpus of a board, with its chat suite (null when it has none). */
export type CorpusSection<Suite = Record<string, unknown>> = {
  id: string;
  label: string;
  /** The board corpus: the one the table's chat column and Overall rest on. */
  board: boolean;
  /**
   * Every comparison answer of this corpus was also graded by hand (its entry's
   * `review` is full), so its comparison and headlines are the hand-graded
   * numbers (2026-10-23; the scorer's are kept in `comparison.scorer`).
   */
  handGraded: boolean;
  suite: Suite | null;
};

/** The board's corpora in report order, the board corpus first; empty for any other layout. */
export function boardCorpora<Suite = Record<string, unknown>>(
  board: unknown,
): CorpusSection<Suite>[] {
  if (!isRecord(board) || boardFormat(board) !== BOARD_FORMAT) return [];
  if (!Array.isArray(board.corpora)) return [];
  const suites = isRecord(board.suites) ? board.suites : {};
  const chat = isRecord(suites.chat) ? suites.chat : null;
  const entries = board.corpora;
  if (!entries.every((e) => isRecord(e) && typeof e.id === "string" && typeof e.label === "string")) {
    return [];
  }
  const records = entries as Record<string, unknown>[];
  const ids = records.map((e) => e.id as string);
  const boards = records.filter((e) => e.board === true);
  if (new Set(ids).size !== ids.length) return [];
  if (records.length > 0 && boards.length !== 1) return [];
  if (chat && boards.length === 1 && boards[0].id !== chat.fixture) return [];
  return records.map((e) => {
    const isBoard = e.board === true;
    const suite = isBoard ? chat : isRecord(e.chat) ? e.chat : null;
    return {
      id: e.id as string,
      label: e.label as string,
      board: isBoard,
      handGraded: isRecord(e.review) && e.review.full === true,
      suite: suite as Suite | null,
    };
  });
}

/** The board corpus (the table's chat column and Overall), or null when there is none. */
export function boardCorpus<Suite = Record<string, unknown>>(
  board: unknown,
): CorpusSection<Suite> | null {
  return boardCorpora<Suite>(board).find((c) => c.board) ?? null;
}
