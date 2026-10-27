// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Pipeline flags on committed graph nodes.
 *
 * The import pipeline keeps records it is unsure about instead of deleting
 * them, and marks each one with a boolean property on the node (keep, flag,
 * never delete). This module is the interface's single reader of those
 * marks: the graph canvas's "Unresolved (N)" hub (its members, their hover
 * line and their detail panel) and the entity chips all go through
 * {@link nodeFlags}.
 *
 * A flag is set only when its property is exactly boolean ``true``. A string
 * ``"true"``, ``1`` or any other value is an ordinary extracted property, not
 * a pipeline flag.
 *
 * Adding a flag is one entry in {@link NODE_FLAGS}; every surface picks it up.
 */

export interface NodeFlagDefinition {
  /** Property key the pipeline sets to ``true`` on the node. */
  readonly key: string;
  /** Short label for a chip or canvas badge. */
  readonly label: string;
  /** One-sentence explanation for a tooltip. */
  readonly description: string;
}

/** A node flag, with the plain line saying why it leaves the node unresolved. */
export interface NodeFlagEntry extends NodeFlagDefinition {
  /** Why the flag leaves the node unresolved, in plain words (canvas hover, detail panel). */
  readonly reason: string;
}

/** Every pipeline flag the interface knows, in display order. */
export const NODE_FLAGS = [
  {
    key: 'orphan',
    label: 'Unconnected',
    reason: 'No connections to other nodes',
    description:
      'Had no relationship when it was committed. Kept because it carries a description or evidence; it may gain relationships later.',
  },
  {
    key: 'descriptor_reference',
    label: 'Reference',
    reason: 'Named by a description, not a proper name',
    description:
      'Labelled by a reference rather than a name (for example "the count", "Prince Andrew\'s wife" or "the army").',
  },
  {
    key: 'low_plausibility',
    label: 'Low plausibility',
    reason: 'Its name may not be a real entity',
    description:
      'Its name scored below the plausibility bar. Kept because it carries a description or evidence; its score is in the plausibility property.',
  },
  {
    key: 'unplaced_type',
    label: 'Unplaced type',
    reason: 'Its type fits none of the core types',
    description:
      'No core type answers to the type written for it, so it is kept as a Concept; the written type is in the written_type property.',
  },
  {
    key: 'unevidenced',
    label: 'Unevidenced',
    reason: 'The sentence it cites does not name it',
    description:
      'The sentence it cites does not name it. Kept because it carries a description; why is in the evidence_reason property.',
  },
  {
    key: 'excluded_name',
    label: 'Excluded name',
    reason: "Its name is on the domain's exclusion list",
    description:
      "Its name is one of the domain's exclusion examples. Kept because it carries a description or evidence; the rule that matched is in the excluded_by property.",
  },
] as const satisfies readonly NodeFlagEntry[];

/** The property keys the node flags write (hidden from a node's facts; its reasons show instead). */
export const NODE_FLAG_KEYS: ReadonlySet<string> = new Set(NODE_FLAGS.map((flag) => flag.key));

export type NodeFlag = (typeof NODE_FLAGS)[number];

/**
 * The pipeline flags a node carries, in {@link NODE_FLAGS} order.
 *
 * @param properties - The node's ``properties`` bag (the canvas keeps it as
 *   ``content``). ``null`` / ``undefined`` / non-objects carry no flags.
 */
export function nodeFlags(properties: unknown): NodeFlag[] {
  return flagsOf(NODE_FLAGS, properties);
}

/**
 * The entries of a flag table a properties bag carries, in table order: each
 * whose key holds exactly boolean ``true``. The one reader of a pipeline flag
 * table (node flags here, edge flags in ``edgeFlags.ts``).
 */
export function flagsOf<T extends { readonly key: string }>(
  table: readonly T[],
  properties: unknown,
): T[] {
  if (properties === null || typeof properties !== 'object') return [];
  const bag = properties as Record<string, unknown>;
  return table.filter((flag) => bag[flag.key] === true);
}

/** True when the node carries at least one pipeline flag. */
export function hasNodeFlags(properties: unknown): boolean {
  return nodeFlags(properties).length > 0;
}
