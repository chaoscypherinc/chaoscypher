// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from 'vitest';

import { EVIDENCE_QUOTE_CHARS, graphBlockLines, shapeCaption } from '../graphBlocks';
import sample from './graphBlocks.sample.json';

/**
 * The tool item's graph lines mirror the Python renderer
 * (`context_render.render_retrieved_context`). `graphBlocks.sample.json` is
 * pinned on both sides: `test_context_render.py` renders the same result and
 * expects the same lines, so neither rendering can drift on its own.
 */
describe('graphBlockLines', () => {
  it('renders the shared sample exactly as the Python renderer does', () => {
    expect(graphBlockLines(sample.result)).toEqual(sample.lines);
    expect(EVIDENCE_QUOTE_CHARS).toBe(sample.quote_chars);
    // the model reads the same lines inside the fence; the tool item shows them without it
    expect(sample.prompt_lines.slice(2, -1)).toEqual(sample.lines);
  });

  it('renders an aggregate group with its quotes, markers and the rows past the listed ones', () => {
    const result = {
      shape: 'aggregate',
      graph_context: {
        aggregates: [
          {
            entity: 'Count Rostóv',
            groups: [
              {
                label: 'child_of',
                count: 3,
                rows: [
                  {
                    source: 'Natásha',
                    target: 'Count Rostóv',
                    sentence: 'She was the youngest girl.',
                    marker: 'C2:S4',
                  },
                  { source: 'Nicholas', target: 'Count Rostóv', sentence: null },
                ],
              },
              {
                label: 'located_in',
                count: 50,
                count_capped: true,
                rows: [{ source: 'the Kremlin', target: 'Moscow', sentence: null }],
              },
            ],
          },
        ],
      },
    };
    expect(graphBlockLines(result)).toEqual([
      '- Count Rostóv, child_of (3): Natásha child_of Count Rostóv "She was the youngest girl."'
        + ' [C2:S4]; Nicholas child_of Count Rostóv; and 1 more',
      '- Count Rostóv, located_in (50+): the Kremlin located_in Moscow; and 49 more',
    ]);
  });

  it('renders a walk and a gap', () => {
    const result = {
      shape: 'path',
      graph_context: {
        walks: [
          {
            from: 'Dólokhov',
            to: 'Pierre',
            hops: [
              {
                source: 'Dólokhov',
                label: 'lives_with',
                target: 'Anatole',
                sentence: 'Dólokhov lived with Anatole.',
                marker: 'C7:S3',
              },
              { source: 'Anatole', label: 'friend_of', target: 'Pierre', sentence: null },
            ],
          },
        ],
        walk_gaps: [
          { from: 'Dólokhov', to: 'nobody', max_hops: 3 },
          { from: 'Pierre', to: 'nobody', max_hops: 2, left_out: true },
        ],
      },
    };
    expect(graphBlockLines(result)).toEqual([
      '- walk: Dólokhov lives_with Anatole "Dólokhov lived with Anatole." [C7:S3]; Anatole friend_of Pierre',
      '- no chain of up to 3 relations connects Dólokhov and nobody in the graph',
      '- no evidenced chain of up to 2 relations connects Pierre and nobody among the relations the walk read',
    ]);
  });

  it('renders nothing for a result without a shape, or for anything not a result', () => {
    const blocks = { graph_context: { walk_gaps: [{ from: 'a', to: 'b', max_hops: 2 }] } };
    expect(graphBlockLines(blocks)).toEqual([]);
    expect(graphBlockLines(null)).toEqual([]);
    expect(graphBlockLines('{"shape": "path"}')).toEqual([]);
    expect(graphBlockLines({ shape: 'lookup' })).toEqual([]);
  });
});

describe('shapeCaption', () => {
  it('names the shape and the router reason', () => {
    expect(shapeCaption(sample.result)).toBe(sample.caption);
    const path = {
      shape: 'path',
      retrieval_stats: { shape: 'path', shape_reason: 'two named entities and connected' },
    };
    expect(shapeCaption(path)).toBe('Shape: path (two named entities and connected)');
  });

  it('leaves the reason out when there is none, and is null without a shape', () => {
    expect(shapeCaption({ shape: 'lookup', retrieval_stats: {} })).toBe('Shape: lookup');
    expect(shapeCaption({ retrieval_stats: { shape: 'path' } })).toBeNull();
    expect(shapeCaption(undefined)).toBeNull();
  });
});
