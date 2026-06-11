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
});
