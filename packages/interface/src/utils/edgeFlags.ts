// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Pipeline flags on committed graph edges.
 *
 * The edge counterpart of ``nodeFlags.ts``: the import pipeline keeps a
 * relationship it would once have dropped and marks it with a boolean
 * property on the edge (keep, flag, never delete). Edges have no canvas
 * region; each flag says how the canvas draws a flagged edge
 * ({@link EdgeFlagDefinition.draw}): an unevidenced edge dashed, the others
 * (capped, unmatched label, type mismatch) thin ({@link FLAGGED_EDGE_SIZE}).
 * The edge detail names every flag an edge carries and the detail the
 * pipeline gave beside it (``EdgeFlagNotice``): the reason, or the label the
 * model wrote.
 *
 * A flag is set only when its property is exactly boolean ``true`` (read by
 * the shared ``flagsOf``); any other value is an ordinary extracted property.
 * The list matches the core's ``name_identity.PIPELINE_EDGE_FLAG_KEYS``, and
 * each ``reasonKey`` its ``EDGE_FLAG_DETAIL_KEYS`` (a cortex guard test pins
 * them, and the node table beside them).
 *
 * Adding a flag is one entry in {@link EDGE_FLAGS}; every surface picks it up.
 */

import { flagsOf, type NodeFlagDefinition } from './nodeFlags';

/** How the canvas draws an edge carrying the flag. */
export type EdgeFlagDrawing = 'thin' | 'dashed';

export interface EdgeFlagDefinition extends NodeFlagDefinition {
  /** How the canvas draws an edge carrying the flag. */
  readonly draw: EdgeFlagDrawing;
  /** Property shown beside the flag, if any: why it was set, or what the model wrote. */
  readonly reasonKey?: string;
}

/** Every pipeline edge flag the interface knows, in display order. */
export const EDGE_FLAGS = [
  {
    key: 'capped',
    label: 'Capped',
    description:
      'Past a relationship cap (same source and type, entity degree or total) when it was extracted. Kept, ranked after the uncapped relationships, and drawn thin.',
    draw: 'thin',
  },
  {
    key: 'unevidenced',
    label: 'Unevidenced',
    description:
      'No cited sentence states this relationship. Kept with its justification instead of being deleted; left out of chat answers unless the setting that includes unevidenced edges is on.',
    draw: 'dashed',
    reasonKey: 'evidence_reason',
  },
  {
    key: 'unmatched_label',
    label: 'Unmatched label',
    description:
      "No relationship type of the domain answers to the label written for it, so it is kept as related_to; the label as written is shown beside the flag. Ranked after the other relationships and drawn thin.",
    draw: 'thin',
    reasonKey: 'written_label',
  },
  {
    key: 'type_mismatch',
    label: 'Type mismatch',
    description:
      "Its endpoints are not of the types its relationship type allows. Kept with its label instead of being deleted; ranked after the other relationships and drawn thin.",
    draw: 'thin',
    reasonKey: 'type_mismatch_reason',
  },
] as const satisfies readonly EdgeFlagDefinition[];

export type EdgeFlag = (typeof EDGE_FLAGS)[number];

/** Canvas size of an edge carrying a ``thin`` flag (an ordinary edge is 1). */
export const FLAGGED_EDGE_SIZE = 0.4;

/** A flag an edge carries, with the reason the pipeline gave when it gave one. */
export interface CarriedEdgeFlag {
  readonly flag: EdgeFlag;
  readonly reason?: string;
}

/** The property keys the edge flags write: each flag and its reason key. */
export const EDGE_FLAG_PROPERTY_KEYS: readonly string[] = EDGE_FLAGS.flatMap((flag) =>
  'reasonKey' in flag ? [flag.key, flag.reasonKey] : [flag.key],
);

/**
 * The pipeline flags an edge carries, in {@link EDGE_FLAGS} order, each with its reason.
 *
 * The one reader of the edge flags (the canvas reducer and the edge detail).
 *
 * @param properties - The edge's ``properties`` bag. ``null`` / ``undefined``
 *   / non-objects carry no flags; only exactly boolean ``true`` is a flag.
 */
export function edgeFlags(properties: unknown): CarriedEdgeFlag[] {
  return flagsOf(EDGE_FLAGS, properties).map((flag) => {
    const reason =
      'reasonKey' in flag ? (properties as Record<string, unknown>)[flag.reasonKey] : undefined;
    return typeof reason === 'string' && reason.trim() ? { flag, reason } : { flag };
  });
}

/** True when the edge carries at least one pipeline flag. */
export function hasEdgeFlags(properties: unknown): boolean {
  return edgeFlags(properties).length > 0;
}

/** How the canvas draws an edge: thin when a flag it carries says so, dashed likewise. */
export function edgeDrawing(properties: unknown): { thin: boolean; dashed: boolean } {
  const draws = new Set(edgeFlags(properties).map(({ flag }) => flag.draw));
  return { thin: draws.has('thin'), dashed: draws.has('dashed') };
}
