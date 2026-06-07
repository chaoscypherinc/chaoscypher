// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Canonical set of system / provenance property keys — extraction bookkeeping
 * that is not user-authored "fact" data. These are hidden from the
 * front-and-center properties lists and surfaced (read-only) in the detail
 * metadata cards instead. Single source of truth shared by the graph-canvas
 * facts view (`NodePropertiesForm`) and the entity/relationship detail pages.
 */
export const SYSTEM_PROPERTY_KEYS: ReadonlySet<string> = new Set([
  'source_document_id',
  'source_document_name',
  'source_type',
  'ingested_at',
  'extracted_at',
  'embedding',
]);

/**
 * System / provenance keys hidden from a relationship's editable properties
 * list. Extends the shared {@link SYSTEM_PROPERTY_KEYS} with the per-edge
 * extraction signals (confidence, sentence reference, source chunk index)
 * that move into the relationship metadata card.
 */
export const RELATIONSHIP_SYSTEM_KEYS: ReadonlySet<string> = new Set([
  'confidence',
  'sent_ref',
  'chunk_index',
  ...SYSTEM_PROPERTY_KEYS,
]);
