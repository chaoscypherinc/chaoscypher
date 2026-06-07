// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * LLM Content Parsing Utilities
 *
 * Format detection and parsing for LLM extraction output in both
 * pipe-delimited (V2) and JSON (flat-items) formats.
 */

export interface Entity {
  name: string;
  type?: string;
  entity_type?: string;
  description?: string;
  aliases?: string[];
  confidence?: number;
  sent_ref?: string;
  properties?: Record<string, unknown>;
}

export interface Relationship {
  source?: number;
  target?: number;
  type?: string;
  source_name?: string;
  target_name?: string;
  relationship_type?: string;
  confidence?: number;
  justification?: string;
  sent_ref?: string;
}

interface ParsedData {
  entities: Entity[];
  relationships: Relationship[];
  format: 'json' | 'pipe';
}

/**
 * Sentence reference matcher (e.g. "S3" or "S2-S5") used to detect the
 * sent_ref field in pipe-delimited LLM output.
 */
const SENT_REF_PATTERN = /^S\d+(?:-S\d+)?$/;

/**
 * Parse a pipe-delimited entity line in V2 format:
 *   E|name|type|aliases|confidence|sent_ref|description
 *
 * Returns null for malformed lines (missing sent_ref, wrong field count, etc.).
 */
function parseEntityLine(line: string): Entity | null {
  // V2 format: 6 fields. Description (last) can contain unescaped pipes,
  // so split with limit 5 to keep everything after the 5th pipe in one piece.
  // JS String.split doesn't have a Python-style maxsplit, so we manually
  // recombine the tail.
  const body = line.substring(2);
  const head = body.split('|', 5);
  if (head.length !== 5) return null;

  const tailStart = head.reduce((acc, part) => acc + part.length + 1, 0);
  const description = body.substring(tailStart);

  const [nameRaw, typeRaw, aliasesStr, confidenceStr, sentRefRaw] = head;
  const sentRef = sentRefRaw.trim();
  if (!SENT_REF_PATTERN.test(sentRef)) return null;

  const name = nameRaw.trim();
  if (!name) return null;

  const aliases = aliasesStr.trim()
    ? aliasesStr.split(';').map(a => a.trim()).filter(Boolean)
    : [];
  const confidence = confidenceStr.trim() ? parseFloat(confidenceStr) : undefined;

  return {
    name,
    type: typeRaw.trim() || 'UNKNOWN',
    aliases,
    confidence: !isNaN(confidence ?? NaN) ? confidence : undefined,
    sent_ref: sentRef,
    description: description.trim() || undefined,
  };
}

/**
 * Parse a pipe-delimited relationship line in V2 format:
 *   R|source_index|target_index|type|confidence|sent_ref|justification
 *
 * Source and target are 0-based integer entity indices. Returns null for
 * malformed lines (non-integer indices, missing sent_ref, etc.).
 */
function parseRelationshipLine(line: string): Relationship | null {
  const body = line.substring(2);
  const head = body.split('|', 5);
  if (head.length !== 5) return null;

  const tailStart = head.reduce((acc, part) => acc + part.length + 1, 0);
  const justification = body.substring(tailStart).trim();

  const [sourceStr, targetStr, typeRaw, confidenceStr, sentRefRaw] = head;
  const sentRef = sentRefRaw.trim();
  if (!SENT_REF_PATTERN.test(sentRef)) return null;

  const source = parseInt(sourceStr.trim(), 10);
  const target = parseInt(targetStr.trim(), 10);
  if (!Number.isInteger(source) || !Number.isInteger(target)) return null;

  const confidence = confidenceStr.trim() ? parseFloat(confidenceStr) : undefined;

  return {
    source,
    target,
    type: typeRaw.trim() || 'related_to',
    confidence: !isNaN(confidence ?? NaN) ? confidence : undefined,
    sent_ref: sentRef,
    justification: justification || undefined,
  };
}

/**
 * Parse pipe-delimited LLM output.
 */
function parsePipeFormat(content: string): ParsedData | null {
  const entities: Entity[] = [];
  const relationships: Relationship[] = [];

  // Normalize line endings and split
  const lines = content.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) continue;

    if (line.startsWith('E|')) {
      const entity = parseEntityLine(line);
      if (entity) entities.push(entity);
    } else if (line.startsWith('R|')) {
      const rel = parseRelationshipLine(line);
      if (rel) relationships.push(rel);
    }
    // Ignore P| (property) lines for now - they should be applied to entities
    // Ignore other lines (preamble, etc.)
  }

  // Only return as valid pipe format if we found at least one entity or relationship
  if (entities.length === 0 && relationships.length === 0) {
    return null;
  }

  return { entities, relationships, format: 'pipe' };
}

interface RawJsonItem {
  item_type?: string;
  name?: string;
  entity_type?: string;
  description?: string;
  aliases?: string[];
  confidence?: number;
  properties?: Record<string, unknown>;
  source_name?: string;
  target_name?: string;
  relationship_type?: string;
  justification?: string;
}

/**
 * Try to parse as JSON. Only the canonical flat-items schema is accepted:
 *
 *   { "items": [ { "item_type": "entity", ... }, ... ] }
 *
 * The older "separate entities/relationships arrays" shape is no longer
 * supported.
 */
function tryParseJSON(jsonString: string): { data: ParsedData | null; error: string | null } {
  try {
    const parsed: unknown = JSON.parse(jsonString);

    if (!parsed || typeof parsed !== 'object') {
      return { data: null, error: 'Invalid JSON structure' };
    }

    const items = (parsed as { items?: unknown }).items;
    if (!Array.isArray(items)) {
      return { data: null, error: 'Missing or invalid "items" array' };
    }

    const entities: Entity[] = [];
    const relationships: Relationship[] = [];

    for (const raw of items as RawJsonItem[]) {
      if (raw.item_type === 'entity' && raw.name) {
        entities.push({
          name: raw.name,
          type: raw.entity_type,
          entity_type: raw.entity_type,
          description: raw.description,
          aliases: raw.aliases,
          confidence: raw.confidence,
          properties: raw.properties,
        });
      } else if (raw.item_type === 'relationship') {
        relationships.push({
          source_name: raw.source_name,
          target_name: raw.target_name,
          relationship_type: raw.relationship_type,
          type: raw.relationship_type,
          confidence: raw.confidence,
          justification: raw.justification,
        });
      }
    }

    return { data: { entities, relationships, format: 'json' }, error: null };
  } catch {
    return { data: null, error: 'JSON parse error' };
  }
}

/**
 * Detect format and parse content.
 */
export function parseContent(content: string): { data: ParsedData | null; error: string | null; rawFormat: string } {
  const trimmed = content.trim();

  // Check if it looks like pipe-delimited format (starts with E| or R| line)
  const firstLine = trimmed.split('\n')[0]?.trim() || '';
  const isPipeFormat = firstLine.startsWith('E|') || firstLine.startsWith('R|') ||
    trimmed.includes('\nE|') || trimmed.includes('\nR|');

  if (isPipeFormat) {
    const pipeData = parsePipeFormat(content);
    if (pipeData) {
      return { data: pipeData, error: null, rawFormat: 'pipe' };
    }
    // If pipe parsing failed, fall through to try JSON
  }

  // Try JSON parsing
  const jsonResult = tryParseJSON(content);
  if (jsonResult.data) {
    return { data: jsonResult.data, error: null, rawFormat: 'json' };
  }

  // Both failed
  return {
    data: null,
    error: isPipeFormat
      ? 'Could not parse pipe-delimited format'
      : jsonResult.error || 'Could not parse content',
    rawFormat: isPipeFormat ? 'pipe' : 'json',
  };
}

/** Map a confidence score to a MUI color. */
export function getConfidenceColor(confidence: number | undefined): 'success' | 'warning' | 'error' | 'default' {
  if (confidence === undefined) return 'default';
  if (confidence >= 0.8) return 'success';
  if (confidence >= 0.5) return 'warning';
  return 'error';
}

/** Format a confidence score as a percentage string. */
export function formatConfidence(confidence: number | undefined): string {
  if (confidence === undefined) return '-';
  return `${Math.round(confidence * 100)}%`;
}
