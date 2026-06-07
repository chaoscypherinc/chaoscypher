// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect } from 'vitest';
import {
  parseContent,
  getConfidenceColor,
  formatConfidence,
} from '../parseLLMContent';
import type { Entity, Relationship } from '../parseLLMContent';

// ---------------------------------------------------------------------------
// getConfidenceColor
// ---------------------------------------------------------------------------
describe('getConfidenceColor', () => {
  it('returns "default" for undefined', () => {
    expect(getConfidenceColor(undefined)).toBe('default');
  });

  it('returns "success" for exactly 0.8', () => {
    expect(getConfidenceColor(0.8)).toBe('success');
  });

  it('returns "success" for values above 0.8', () => {
    expect(getConfidenceColor(0.9)).toBe('success');
    expect(getConfidenceColor(1.0)).toBe('success');
  });

  it('returns "warning" for exactly 0.5', () => {
    expect(getConfidenceColor(0.5)).toBe('warning');
  });

  it('returns "warning" for values between 0.5 and 0.8 (exclusive)', () => {
    expect(getConfidenceColor(0.6)).toBe('warning');
    expect(getConfidenceColor(0.79)).toBe('warning');
    expect(getConfidenceColor(0.799)).toBe('warning');
  });

  it('returns "error" for values below 0.5', () => {
    expect(getConfidenceColor(0.49)).toBe('error');
    expect(getConfidenceColor(0.0)).toBe('error');
    expect(getConfidenceColor(0.1)).toBe('error');
  });

  it('boundary just below 0.8 is "warning"', () => {
    expect(getConfidenceColor(0.7999)).toBe('warning');
  });

  it('boundary just below 0.5 is "error"', () => {
    expect(getConfidenceColor(0.4999)).toBe('error');
  });
});

// ---------------------------------------------------------------------------
// formatConfidence
// ---------------------------------------------------------------------------
describe('formatConfidence', () => {
  it('returns "-" for undefined', () => {
    expect(formatConfidence(undefined)).toBe('-');
  });

  it('formats 0 as "0%"', () => {
    expect(formatConfidence(0)).toBe('0%');
  });

  it('formats 1.0 as "100%"', () => {
    expect(formatConfidence(1.0)).toBe('100%');
  });

  it('formats 0.85 as "85%"', () => {
    expect(formatConfidence(0.85)).toBe('85%');
  });

  it('formats 0.5 as "50%"', () => {
    expect(formatConfidence(0.5)).toBe('50%');
  });

  it('rounds to nearest integer (0.456 → "46%")', () => {
    expect(formatConfidence(0.456)).toBe('46%');
  });

  it('rounds 0.995 to "100%"', () => {
    expect(formatConfidence(0.995)).toBe('100%');
  });

  it('rounds 0.004 to "0%"', () => {
    expect(formatConfidence(0.004)).toBe('0%');
  });
});

// ---------------------------------------------------------------------------
// parseContent — JSON format
// ---------------------------------------------------------------------------
describe('parseContent — JSON flat-items format', () => {
  it('parses a valid entity item', () => {
    const json = JSON.stringify({
      items: [
        {
          item_type: 'entity',
          name: 'Alice',
          entity_type: 'PERSON',
          description: 'Main character',
          aliases: ['Al', 'Allie'],
          confidence: 0.95,
          properties: { age: 30 },
        },
      ],
    });

    const result = parseContent(json);
    expect(result.error).toBeNull();
    expect(result.rawFormat).toBe('json');
    expect(result.data).not.toBeNull();

    const entity = result.data!.entities[0] as Entity;
    expect(entity.name).toBe('Alice');
    expect(entity.type).toBe('PERSON');
    expect(entity.entity_type).toBe('PERSON');
    expect(entity.description).toBe('Main character');
    expect(entity.aliases).toEqual(['Al', 'Allie']);
    expect(entity.confidence).toBe(0.95);
    expect(entity.properties).toEqual({ age: 30 });
  });

  it('parses a valid relationship item', () => {
    const json = JSON.stringify({
      items: [
        {
          item_type: 'relationship',
          source_name: 'Alice',
          target_name: 'Bob',
          relationship_type: 'KNOWS',
          confidence: 0.8,
          justification: 'They went to school together',
        },
      ],
    });

    const result = parseContent(json);
    expect(result.error).toBeNull();
    expect(result.rawFormat).toBe('json');
    expect(result.data).not.toBeNull();

    const rel = result.data!.relationships[0] as Relationship;
    expect(rel.source_name).toBe('Alice');
    expect(rel.target_name).toBe('Bob');
    expect(rel.relationship_type).toBe('KNOWS');
    expect(rel.type).toBe('KNOWS');
    expect(rel.confidence).toBe(0.8);
    expect(rel.justification).toBe('They went to school together');
  });

  it('parses mixed entities and relationships', () => {
    const json = JSON.stringify({
      items: [
        { item_type: 'entity', name: 'Alice', entity_type: 'PERSON' },
        { item_type: 'entity', name: 'Bob', entity_type: 'PERSON' },
        {
          item_type: 'relationship',
          source_name: 'Alice',
          target_name: 'Bob',
          relationship_type: 'FRIENDS_WITH',
        },
      ],
    });

    const result = parseContent(json);
    expect(result.error).toBeNull();
    expect(result.data!.entities).toHaveLength(2);
    expect(result.data!.relationships).toHaveLength(1);
    expect(result.data!.format).toBe('json');
  });

  it('skips entity items without a name', () => {
    const json = JSON.stringify({
      items: [
        { item_type: 'entity' },
        { item_type: 'entity', name: 'Valid' },
      ],
    });

    const result = parseContent(json);
    expect(result.data!.entities).toHaveLength(1);
    expect(result.data!.entities[0].name).toBe('Valid');
  });

  it('skips unknown item_type values', () => {
    const json = JSON.stringify({
      items: [
        { item_type: 'unknown_type', name: 'X' },
        { item_type: 'entity', name: 'Real' },
      ],
    });

    const result = parseContent(json);
    expect(result.data!.entities).toHaveLength(1);
    expect(result.data!.relationships).toHaveLength(0);
  });

  it('returns error when items is missing', () => {
    const json = JSON.stringify({ entities: [], relationships: [] });
    const result = parseContent(json);
    expect(result.data).toBeNull();
    expect(result.error).toContain('"items"');
  });

  it('returns error when items is not an array', () => {
    const json = JSON.stringify({ items: 'not-an-array' });
    const result = parseContent(json);
    expect(result.data).toBeNull();
    expect(result.error).toBeTruthy();
  });

  it('returns error for invalid JSON', () => {
    const result = parseContent('{ not valid json }');
    expect(result.data).toBeNull();
    expect(result.error).toBeTruthy();
  });

  it('returns error for non-object JSON (array at root)', () => {
    const result = parseContent('[1, 2, 3]');
    expect(result.data).toBeNull();
    expect(result.error).toBeTruthy();
  });

  it('returns error for JSON null', () => {
    const result = parseContent('null');
    expect(result.data).toBeNull();
    expect(result.error).toBeTruthy();
  });

  it('handles items array with zero items', () => {
    const json = JSON.stringify({ items: [] });
    const result = parseContent(json);
    // Empty items array is valid JSON, data should be returned with empty arrays
    expect(result.rawFormat).toBe('json');
    expect(result.data).not.toBeNull();
    expect(result.data!.entities).toHaveLength(0);
    expect(result.data!.relationships).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// parseContent — pipe-delimited format
// ---------------------------------------------------------------------------
describe('parseContent — pipe-delimited format', () => {
  it('parses a minimal valid entity line', () => {
    // E|name|type|aliases|confidence|sent_ref|description
    const content = 'E|Alice|PERSON||0.9|S1|Main character';
    const result = parseContent(content);
    expect(result.error).toBeNull();
    expect(result.rawFormat).toBe('pipe');
    expect(result.data).not.toBeNull();
    expect(result.data!.format).toBe('pipe');

    const entity = result.data!.entities[0];
    expect(entity.name).toBe('Alice');
    expect(entity.type).toBe('PERSON');
    expect(entity.confidence).toBeCloseTo(0.9);
    expect(entity.sent_ref).toBe('S1');
    expect(entity.description).toBe('Main character');
    expect(entity.aliases).toEqual([]);
  });

  it('parses entity with aliases', () => {
    const content = 'E|Alice|PERSON|Al;Allie|0.9|S1|';
    const result = parseContent(content);
    expect(result.data!.entities[0].aliases).toEqual(['Al', 'Allie']);
  });

  it('parses entity with a sent_ref range (S2-S5)', () => {
    const content = 'E|Bob|PERSON||0.8|S2-S5|A person';
    const result = parseContent(content);
    expect(result.data!.entities[0].sent_ref).toBe('S2-S5');
  });

  it('defaults type to UNKNOWN when empty', () => {
    const content = 'E|Alice|||0.9|S1|desc';
    const result = parseContent(content);
    expect(result.data!.entities[0].type).toBe('UNKNOWN');
  });

  it('parses entity with no confidence (empty field)', () => {
    const content = 'E|Alice|PERSON|||S1|desc';
    const result = parseContent(content);
    expect(result.data!.entities[0].confidence).toBeUndefined();
  });

  it('parses entity with no description (empty tail)', () => {
    const content = 'E|Alice|PERSON||0.9|S1|';
    const result = parseContent(content);
    expect(result.data!.entities[0].description).toBeUndefined();
  });

  it('description can contain pipe characters (tail kept intact)', () => {
    const content = 'E|Alice|PERSON||0.9|S1|Has pipe|in description';
    const result = parseContent(content);
    expect(result.data!.entities[0].description).toBe('Has pipe|in description');
  });

  it('skips entity lines with invalid sent_ref', () => {
    const content = 'E|Alice|PERSON||0.9|INVALID|desc\nE|Bob|PERSON||0.8|S1|valid';
    const result = parseContent(content);
    expect(result.data!.entities).toHaveLength(1);
    expect(result.data!.entities[0].name).toBe('Bob');
  });

  it('skips entity lines with empty name', () => {
    const content = 'E||PERSON||0.9|S1|desc\nE|Bob|PERSON||0.8|S2|valid';
    const result = parseContent(content);
    expect(result.data!.entities).toHaveLength(1);
    expect(result.data!.entities[0].name).toBe('Bob');
  });

  it('parses a valid relationship line', () => {
    // R|source_idx|target_idx|type|confidence|sent_ref|justification
    const content = 'E|Alice|PERSON||0.9|S1|\nE|Bob|PERSON||0.8|S2|\nR|0|1|KNOWS|0.75|S3|They met at school';
    const result = parseContent(content);
    expect(result.data!.relationships).toHaveLength(1);
    const rel = result.data!.relationships[0];
    expect(rel.source).toBe(0);
    expect(rel.target).toBe(1);
    expect(rel.type).toBe('KNOWS');
    expect(rel.confidence).toBeCloseTo(0.75);
    expect(rel.sent_ref).toBe('S3');
    expect(rel.justification).toBe('They met at school');
  });

  it('defaults relationship type to "related_to" when empty', () => {
    const content = 'R|0|1||0.5|S1|just';
    const result = parseContent(content);
    expect(result.data!.relationships[0].type).toBe('related_to');
  });

  it('parses relationship with no confidence', () => {
    const content = 'R|0|1|KNOWS||S1|';
    const result = parseContent(content);
    expect(result.data!.relationships[0].confidence).toBeUndefined();
  });

  it('parses relationship with no justification', () => {
    const content = 'R|0|1|KNOWS|0.8|S1|';
    const result = parseContent(content);
    expect(result.data!.relationships[0].justification).toBeUndefined();
  });

  it('skips relationship lines with invalid sent_ref', () => {
    const content = 'R|0|1|KNOWS|0.8|BADREF|just\nR|0|1|KNOWS|0.8|S1|good';
    const result = parseContent(content);
    expect(result.data!.relationships).toHaveLength(1);
  });

  it('skips relationship lines with non-integer source index', () => {
    const content = 'R|abc|1|KNOWS|0.8|S1|just\nR|0|1|KNOWS|0.8|S2|good';
    const result = parseContent(content);
    expect(result.data!.relationships).toHaveLength(1);
  });

  it('skips relationship lines with non-integer target index', () => {
    const content = 'R|0|abc|KNOWS|0.8|S1|just\nR|0|1|KNOWS|0.8|S2|good';
    const result = parseContent(content);
    expect(result.data!.relationships).toHaveLength(1);
  });

  it('ignores P| lines without crashing', () => {
    const content = 'E|Alice|PERSON||0.9|S1|desc\nP|Alice|age|30\nR|0|0|SELF|0.5|S1|';
    const result = parseContent(content);
    expect(result.data!.entities).toHaveLength(1);
    expect(result.data!.relationships).toHaveLength(1);
  });

  it('ignores arbitrary non-E/R lines (preamble text)', () => {
    const content = 'Here is the extraction:\nE|Alice|PERSON||0.9|S1|desc';
    const result = parseContent(content);
    expect(result.data!.entities).toHaveLength(1);
  });

  it('detects pipe format when E| appears after a newline (not first line)', () => {
    const content = 'Preamble line\nE|Alice|PERSON||0.9|S1|desc';
    const result = parseContent(content);
    expect(result.rawFormat).toBe('pipe');
    expect(result.data!.entities).toHaveLength(1);
  });

  it('detects pipe format when R| appears after a newline', () => {
    const content = 'Preamble line\nR|0|1|KNOWS|0.8|S1|';
    const result = parseContent(content);
    expect(result.rawFormat).toBe('pipe');
    expect(result.data!.relationships).toHaveLength(1);
  });

  it('normalizes CRLF line endings', () => {
    const content = 'E|Alice|PERSON||0.9|S1|desc\r\nE|Bob|PERSON||0.8|S2|desc2';
    const result = parseContent(content);
    expect(result.data!.entities).toHaveLength(2);
  });

  it('normalizes CR line endings', () => {
    const content = 'E|Alice|PERSON||0.9|S1|desc\rE|Bob|PERSON||0.8|S2|desc2';
    const result = parseContent(content);
    expect(result.data!.entities).toHaveLength(2);
  });

  it('skips empty lines in pipe format', () => {
    const content = 'E|Alice|PERSON||0.9|S1|desc\n\n\nE|Bob|PERSON||0.8|S2|desc2';
    const result = parseContent(content);
    expect(result.data!.entities).toHaveLength(2);
  });

  it('returns error when pipe content has no valid entities or relationships', () => {
    // Looks like pipe format (has E| prefix) but all lines are malformed
    const content = 'E|INVALID_NO_SENT_REF||0.9||desc';
    const result = parseContent(content);
    expect(result.data).toBeNull();
    expect(result.error).toBeTruthy();
    expect(result.rawFormat).toBe('pipe');
  });

  it('parses relationship with only R| lines (no entity lines)', () => {
    const content = 'R|0|1|KNOWS|0.9|S1|justification text';
    const result = parseContent(content);
    expect(result.data).not.toBeNull();
    expect(result.data!.relationships).toHaveLength(1);
    expect(result.data!.entities).toHaveLength(0);
  });

  it('handles relationship with sent_ref range', () => {
    const content = 'R|0|1|KNOWS|0.9|S1-S3|justification';
    const result = parseContent(content);
    expect(result.data!.relationships[0].sent_ref).toBe('S1-S3');
  });
});

// ---------------------------------------------------------------------------
// parseContent — empty / whitespace input
// ---------------------------------------------------------------------------
describe('parseContent — empty and whitespace inputs', () => {
  it('returns error for empty string', () => {
    const result = parseContent('');
    expect(result.data).toBeNull();
    expect(result.error).toBeTruthy();
  });

  it('returns error for whitespace-only string', () => {
    const result = parseContent('   \n\t\n  ');
    expect(result.data).toBeNull();
    expect(result.error).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// parseContent — fallback / ambiguous paths
// ---------------------------------------------------------------------------
describe('parseContent — fallback paths', () => {
  it('falls through from pipe detection to JSON error when pipe parsing yields nothing', () => {
    // Has E| text in newline context but it is not a real pipe line
    const content = '\nE|bad|line';
    // The parser will detect pipe format then fail (malformed line: no sent_ref)
    const result = parseContent(content);
    expect(result.data).toBeNull();
    expect(result.rawFormat).toBe('pipe');
    expect(result.error).toBeTruthy();
  });

  it('plain text that is not pipe or valid JSON returns json-path error', () => {
    const result = parseContent('Just some plain text without any structure.');
    expect(result.data).toBeNull();
    expect(result.error).toBeTruthy();
    expect(result.rawFormat).toBe('json');
  });

  it('markdown-like content returns json-path error', () => {
    const result = parseContent('# Heading\n\n- item one\n- item two\n');
    expect(result.data).toBeNull();
    expect(result.rawFormat).toBe('json');
  });

  it('JSON string (primitive) returns invalid structure error', () => {
    const result = parseContent('"just a string"');
    expect(result.data).toBeNull();
    expect(result.error).toBeTruthy();
  });

  it('JSON number (primitive) returns invalid structure error', () => {
    const result = parseContent('42');
    expect(result.data).toBeNull();
    expect(result.error).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// parseContent — entity field edge cases
// ---------------------------------------------------------------------------
describe('parseContent — entity field edge cases', () => {
  it('trims whitespace from entity name', () => {
    const content = 'E|  Alice  |PERSON||0.9|S1|desc';
    const result = parseContent(content);
    expect(result.data!.entities[0].name).toBe('Alice');
  });

  it('trims whitespace from entity type', () => {
    const content = 'E|Alice|  PERSON  ||0.9|S1|desc';
    const result = parseContent(content);
    expect(result.data!.entities[0].type).toBe('PERSON');
  });

  it('trims whitespace from sent_ref', () => {
    const content = 'E|Alice|PERSON||0.9| S1 |desc';
    const result = parseContent(content);
    expect(result.data!.entities[0].sent_ref).toBe('S1');
  });

  it('filters empty aliases from semicolon list', () => {
    // e.g. "Al;;Allie" → ['Al', 'Allie']
    const content = 'E|Alice|PERSON|Al;;Allie|0.9|S1|desc';
    const result = parseContent(content);
    expect(result.data!.entities[0].aliases).toEqual(['Al', 'Allie']);
  });

  it('trims individual aliases', () => {
    const content = 'E|Alice|PERSON| Al ; Allie |0.9|S1|desc';
    const result = parseContent(content);
    expect(result.data!.entities[0].aliases).toEqual(['Al', 'Allie']);
  });

  it('invalid confidence string results in undefined confidence', () => {
    const content = 'E|Alice|PERSON||notanumber|S1|desc';
    const result = parseContent(content);
    expect(result.data!.entities[0].confidence).toBeUndefined();
  });

  it('handles entity line with too few pipe fields (skipped)', () => {
    // Only 3 fields in body → head.length < 5 → returns null → skipped
    const content = 'E|Alice|PERSON\nE|Bob|PERSON||0.8|S1|valid';
    const result = parseContent(content);
    expect(result.data!.entities).toHaveLength(1);
    expect(result.data!.entities[0].name).toBe('Bob');
  });
});

// ---------------------------------------------------------------------------
// parseContent — relationship field edge cases
// ---------------------------------------------------------------------------
describe('parseContent — relationship field edge cases', () => {
  it('trims whitespace from relationship indices', () => {
    const content = 'R| 0 | 1 |KNOWS|0.8|S1|';
    const result = parseContent(content);
    expect(result.data!.relationships[0].source).toBe(0);
    expect(result.data!.relationships[0].target).toBe(1);
  });

  it('invalid relationship confidence results in undefined', () => {
    const content = 'R|0|1|KNOWS|badvalue|S1|';
    const result = parseContent(content);
    expect(result.data!.relationships[0].confidence).toBeUndefined();
  });

  it('handles relationship line with too few pipe fields (skipped)', () => {
    const content = 'R|0|1\nR|0|1|KNOWS|0.8|S2|good';
    const result = parseContent(content);
    expect(result.data!.relationships).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// parseContent — data shape / format field
// ---------------------------------------------------------------------------
describe('parseContent — ParsedData shape', () => {
  it('json-parsed result has format="json"', () => {
    const json = JSON.stringify({ items: [{ item_type: 'entity', name: 'X' }] });
    const result = parseContent(json);
    expect(result.data!.format).toBe('json');
  });

  it('pipe-parsed result has format="pipe"', () => {
    const result = parseContent('E|Alice|PERSON||0.9|S1|desc');
    expect(result.data!.format).toBe('pipe');
  });

  it('successful parse always has error=null', () => {
    const json = JSON.stringify({ items: [] });
    const result = parseContent(json);
    expect(result.error).toBeNull();
  });

  it('failed parse always has data=null', () => {
    const result = parseContent('not valid');
    expect(result.data).toBeNull();
  });
});
