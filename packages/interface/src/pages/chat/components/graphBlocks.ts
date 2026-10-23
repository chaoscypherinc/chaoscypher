// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * The graph lines of a `graphrag_search` result, as the chat model read them.
 *
 * Mirrors the Python renderer (`context_render.render_retrieved_context`:
 * `_aggregate_lines`, `_walk_line`, `_gap_line`, and `graphrag_blocks.quote`)
 * line for line, so the tool item shows the block that answered the question
 * in the words the model saw. Both sides pin `__tests__/graphBlocks.sample.json`;
 * change a format on one side and that side's test fails until the sample,
 * and the other side, follow.
 *
 * The model reads these lines inside the untrusted-document fence (the
 * `<untrusted_document>` tags and a `[GRAPH EVIDENCE]` header: the quoted
 * sentences are document text, `context_render._evidence_region`); the tool
 * item shows the lines, not the prompt, so the mirror renders them WITHOUT
 * the fence tags. The sample pins both (`lines`, `prompt_lines`).
 */

/** The longest evidence sentence a block quotes, in characters (`graphrag_blocks.EVIDENCE_QUOTE_CHARS`). */
export const EVIDENCE_QUOTE_CHARS = 140;

type Fields = Record<string, unknown>;

function fields(value: unknown): Fields {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as Fields) : {};
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function text(value: unknown): string {
  return value === null || value === undefined ? '' : String(value);
}

/** A sentence as a block quotes it: whitespace folded, cut at a word to {@link EVIDENCE_QUOTE_CHARS} with `…`. */
function quote(sentence: string): string {
  // code points, as Python's len counts them
  const chars = Array.from(sentence.trim().split(/\s+/).join(' '));
  if (chars.length <= EVIDENCE_QUOTE_CHARS) return chars.join('');
  let cut = chars.slice(0, EVIDENCE_QUOTE_CHARS - 1).join('');
  if (chars[EVIDENCE_QUOTE_CHARS - 1] !== ' ' && cut.includes(' ')) {
    cut = cut.slice(0, cut.lastIndexOf(' '));
  }
  return `${cut.replace(/[ ,;:]+$/, '')}…`;
}

/** `source label target "sentence" [C2:S4]` for one aggregate row or walk hop; parts it lacks left out. */
function factText(row: Fields, label: string): string {
  let fact = `${text(row.source)} ${label} ${text(row.target)}`;
  if (row.sentence) fact += ` "${quote(text(row.sentence))}"`;
  if (row.marker) fact += ` [${text(row.marker)}]`;
  return fact;
}

/** `- entity, label (count): fact; …; and n more`, one line per group; a capped count shows as `(50+)`. */
function aggregateLines(block: Fields): string[] {
  const entity = text(block.entity);
  return list(block.groups).map((item) => {
    const group = fields(item);
    const label = text(group.label);
    const rows = list(group.rows).map(fields);
    const count = typeof group.count === 'number' ? Math.trunc(group.count) : rows.length;
    const items = rows.map((row) => factText(row, label));
    if (count > rows.length) items.push(`and ${count - rows.length} more`);
    const shown = group.count_capped ? `${count}+` : String(count);
    return `- ${entity}, ${label} (${shown}): ${items.join('; ')}`;
  });
}

/** `- walk: a rel b "sentence" [C7:S3]; b rel c` for one walk. */
function walkLine(walk: Fields): string {
  const hops = list(walk.hops).map(fields);
  return `- walk: ${hops.map((hop) => factText(hop, text(hop.label))).join('; ')}`;
}

/**
 * `- no chain of up to 3 relations connects a and b in the graph` for a pair no walk joins.
 *
 * A gap whose walk left edges out (`left_out`) claims only what the walk read:
 * `- no evidenced chain of up to 3 relations connects a and b among the relations the walk read`.
 */
function gapLine(gap: Fields): string {
  const ends = `${text(gap.from)} and ${text(gap.to)}`;
  if (gap.left_out) {
    return `- no evidenced chain of up to ${text(gap.max_hops)} relations connects ${ends} among the relations the walk read`;
  }
  return `- no chain of up to ${text(gap.max_hops)} relations connects ${ends} in the graph`;
}

/**
 * The aggregate, walk and gap lines of a parsed `graphrag_search` result, in the renderer's order.
 *
 * Empty for a result that carries no `shape` (one from before the router, or
 * any other tool's result) and for a value that is not a result object.
 */
export function graphBlockLines(result: unknown): string[] {
  const parsed = fields(result);
  if (!parsed.shape) return [];
  const graph = fields(parsed.graph_context);
  return [
    ...list(graph.aggregates).flatMap((block) => aggregateLines(fields(block))),
    ...list(graph.walks).map((walk) => walkLine(fields(walk))),
    ...list(graph.walk_gaps).map((gap) => gapLine(fields(gap))),
  ];
}

/**
 * `Shape: path (two named entities and connected)`: the question's shape and why the router chose it.
 *
 * The reason (`retrieval_stats.shape_reason`) is left out when there is none;
 * null for a result without a `shape`.
 */
export function shapeCaption(result: unknown): string | null {
  const parsed = fields(result);
  if (!parsed.shape) return null;
  const reason = text(fields(parsed.retrieval_stats).shape_reason);
  return reason ? `Shape: ${text(parsed.shape)} (${reason})` : `Shape: ${text(parsed.shape)}`;
}
