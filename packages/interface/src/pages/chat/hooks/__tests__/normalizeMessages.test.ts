// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from 'vitest';
import type { ChatMessage } from '../../../../types';
import { normalizeMessages } from '../normalizeMessages';

function msg(extra: ChatMessage['extra_metadata']): ChatMessage {
  return { role: 'assistant', content: 'x', extra_metadata: extra };
}

describe('normalizeMessages', () => {
  it('flattens referenced_entities from extra_metadata', () => {
    const entities = { 'abc-123': { id: 'abc-123', type: 'node' as const, label: 'Pierre' } };
    const out = normalizeMessages([msg({ referenced_entities: entities })]);
    expect(out[0].referenced_entities).toEqual(entities);
  });

  it('flattens legacy entity_references key (queued-worker rows 2026-06-09..10)', () => {
    const entities = { 'abc-123': { id: 'abc-123', type: 'node' as const, label: 'Pierre' } };
    const out = normalizeMessages([msg({ entity_references: entities })]);
    expect(out[0].referenced_entities).toEqual(entities);
  });

  it('prefers the canonical key when both are present', () => {
    const canonical = { a: { id: 'a', type: 'node' as const, label: 'A' } };
    const legacy = { b: { id: 'b', type: 'node' as const, label: 'B' } };
    const out = normalizeMessages([
      msg({ referenced_entities: canonical, entity_references: legacy }),
    ]);
    expect(out[0].referenced_entities).toEqual(canonical);
  });

  it('passes through messages without extra_metadata', () => {
    const out = normalizeMessages([{ role: 'user', content: 'hi' }]);
    expect(out[0].referenced_entities).toBeUndefined();
    expect(out[0].content).toBe('hi');
  });

  it('merges per_citation verdicts into chunk citations', () => {
    const input = msg({
      chunk_citations: {
        'c1:S1': { chunk_id: 'c1', sentence_refs: 'S1', label: 'a.txt' },
        'c2:S1': { chunk_id: 'c2', sentence_refs: 'S1', label: 'b.txt' },
      },
      validation: {
        verdict: 'partial',
        reason: '1 of 2 verified',
        per_citation: {
          'c1:S1': { verdict: 'correct', reason: 'found' },
          'c2:S1': { verdict: 'wrong', reason: 'missing' },
        },
      },
    } as ChatMessage['extra_metadata']);
    const out = normalizeMessages([input]);
    expect(out[0].chunk_citations?.['c1:S1'].validation_verdict).toBe('correct');
    expect(out[0].chunk_citations?.['c2:S1'].validation_verdict).toBe('wrong');
  });

  it('does not mutate the input message when merging verdicts (cache safety)', () => {
    // The input objects live in the TanStack Query cache — clone, never
    // mutate (CLAUDE.md § State management; 2026-07-27 audit regression).
    const cite = { chunk_id: 'c1', sentence_refs: 'S1', label: 'a.txt' };
    const input = msg({
      chunk_citations: { 'c1:S1': cite },
      validation: {
        verdict: 'correct',
        reason: 'verified',
        per_citation: { 'c1:S1': { verdict: 'correct', reason: 'found' } },
      },
    } as ChatMessage['extra_metadata']);
    const out = normalizeMessages([input]);
    expect(out[0].chunk_citations?.['c1:S1'].validation_verdict).toBe('correct');
    // The cached citation object must be untouched…
    expect('validation_verdict' in cite).toBe(false);
    // …and the normalized map must be a new object, not the cached one.
    expect(out[0].chunk_citations).not.toBe(input.extra_metadata!.chunk_citations);
  });
});
