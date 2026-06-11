// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  handleStreamEvent,
  createAccumulator,
  type StreamAccumulator,
  type EventDispatchers,
  type DoneCallbacks,
} from '../handleStreamEvent';
import { logger } from '../../../../utils/logger';

// ---------------------------------------------------------------------------
// Logger mock
// ---------------------------------------------------------------------------

vi.mock('../../../../utils/logger', () => ({
  logger: {
    error: vi.fn(),
    warn: vi.fn(),
    info: vi.fn(),
    debug: vi.fn(),
  },
}));

// ---------------------------------------------------------------------------
// Helpers — build minimal mocks
// ---------------------------------------------------------------------------

function makeDispatchers(): EventDispatchers {
  return {
    setMessages: vi.fn<(updater: unknown) => void>(),
    setLoading: vi.fn<(value: boolean) => void>(),
    setIsStreamingActive: vi.fn<(value: boolean) => void>(),
    setContextInfo: vi.fn<(value: unknown) => void>(),
    setError: vi.fn<(value: unknown) => void>(),
    setPendingApproval: vi.fn<(value: unknown) => void>(),
  } as unknown as EventDispatchers;
}

function makeDoneCallbacks(): DoneCallbacks {
  return {
    onDone: vi.fn<(chatId: string, wasNewChat: boolean) => Promise<void>>().mockResolvedValue(
      undefined,
    ),
  };
}

/** Call setMessages' updater with an empty array and return what it returns. */
function applySetMessages(
  dispatchers: EventDispatchers,
  callIndex = 0,
): unknown[] {
  const mock = dispatchers.setMessages as ReturnType<typeof vi.fn>;
  const updater = mock.mock.calls[callIndex][0];
  if (typeof updater === 'function') {
    return updater([]) as unknown[];
  }
  return updater as unknown[];
}

/** Call setMessages' updater with a pre-existing array of messages. */
function applySetMessagesWithPrev(
  dispatchers: EventDispatchers,
  prev: unknown[],
  callIndex = 0,
): unknown[] {
  const mock = dispatchers.setMessages as ReturnType<typeof vi.fn>;
  const updater = mock.mock.calls[callIndex][0];
  if (typeof updater === 'function') {
    return updater(prev) as unknown[];
  }
  return updater as unknown[];
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

let acc: StreamAccumulator;
let dispatchers: EventDispatchers;
let doneCallbacks: DoneCallbacks;

beforeEach(() => {
  vi.clearAllMocks();
  acc = createAccumulator();
  dispatchers = makeDispatchers();
  doneCallbacks = makeDoneCallbacks();
});

// ---------------------------------------------------------------------------
// createAccumulator
// ---------------------------------------------------------------------------

describe('createAccumulator', () => {
  it('returns the correct initial shape', () => {
    const a = createAccumulator();
    expect(a.accumulatedThinking).toBe('');
    expect(a.allToolCalls).toEqual([]);
    expect(a.allCachedToolCalls).toEqual([]);
    expect(a.iterationContents).toEqual([]);
    expect(a.currentPhaseContent).toBe('');
    expect(a.streamingTiming).toEqual({});
    expect(a.toolTimings).toEqual([]);
    expect(a.isDone).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// default branch — unknown event types
// ---------------------------------------------------------------------------

describe('handleStreamEvent — default branch', () => {
  it('warns on unknown event types with structured payload', () => {
    handleStreamEvent(
      { type: 'surprise', foo: 1 } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(logger.warn).toHaveBeenCalledWith(
      'unknown_sse_event_type',
      expect.objectContaining({ type: 'surprise' }),
    );
  });

  it('returns 0 for unknown event types', () => {
    const result = handleStreamEvent(
      { type: 'totally_unknown' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(result).toBe(0);
  });

  it('includes the event keys in the warn payload', () => {
    handleStreamEvent(
      { type: 'mystery', field_a: 'x', field_b: 42 } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(logger.warn).toHaveBeenCalledWith(
      'unknown_sse_event_type',
      expect.objectContaining({
        type: 'mystery',
        keys: expect.arrayContaining(['type', 'field_a', 'field_b']),
      }),
    );
  });
});

// ---------------------------------------------------------------------------
// iteration_progress
// ---------------------------------------------------------------------------

describe('handleStreamEvent — iteration_progress', () => {
  it('pushes non-empty currentPhaseContent to iterationContents and clears it', () => {
    acc.currentPhaseContent = 'phase one';
    const result = handleStreamEvent(
      { type: 'iteration_progress' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(acc.iterationContents).toEqual(['phase one']);
    expect(acc.currentPhaseContent).toBe('');
    expect(result).toBeGreaterThan(0);
  });

  it('does NOT push whitespace-only currentPhaseContent', () => {
    acc.currentPhaseContent = '   ';
    handleStreamEvent(
      { type: 'iteration_progress' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(acc.iterationContents).toEqual([]);
    expect(acc.currentPhaseContent).toBe('');
  });

  it('does NOT push empty currentPhaseContent', () => {
    acc.currentPhaseContent = '';
    handleStreamEvent(
      { type: 'iteration_progress' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(acc.iterationContents).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// content
// ---------------------------------------------------------------------------

describe('handleStreamEvent — content', () => {
  it('updates currentPhaseContent from data.accumulated', () => {
    handleStreamEvent(
      { type: 'content', accumulated: 'hello world' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(acc.currentPhaseContent).toBe('hello world');
  });

  it('calls setMessages with the accumulated content', () => {
    handleStreamEvent(
      { type: 'content', accumulated: 'hello' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(dispatchers.setMessages).toHaveBeenCalledOnce();
  });

  it('appends a new assistant message when none exists', () => {
    handleStreamEvent(
      { type: 'content', accumulated: 'hello' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    const msgs = applySetMessages(dispatchers);
    expect(msgs).toHaveLength(1);
    expect((msgs[0] as { role: string; content: string }).role).toBe('assistant');
    expect((msgs[0] as { role: string; content: string }).content).toBe('hello');
  });

  it('updates the last assistant message when one already exists', () => {
    handleStreamEvent(
      { type: 'content', accumulated: 'updated' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    const prev = [{ role: 'assistant', content: 'old' }];
    const msgs = applySetMessagesWithPrev(dispatchers, prev);
    expect(msgs).toHaveLength(1);
    expect((msgs[0] as { content: string }).content).toBe('updated');
  });

  it('extracts <think>...</think> into thinking field and removes it from content', () => {
    handleStreamEvent(
      {
        type: 'content',
        accumulated: '<think>inner reasoning</think>visible text',
      } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    const msgs = applySetMessages(dispatchers);
    const msg = msgs[0] as { content: string; thinking: string };
    expect(msg.content).toBe('visible text');
    expect(msg.thinking).toBe('inner reasoning');
  });

  it('joins previous iterationContents with separator', () => {
    acc.iterationContents = ['first phase'];
    handleStreamEvent(
      { type: 'content', accumulated: 'second phase' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    const msgs = applySetMessages(dispatchers);
    const msg = msgs[0] as { content: string };
    expect(msg.content).toContain('first phase');
    expect(msg.content).toContain('---');
    expect(msg.content).toContain('second phase');
  });

  it('includes allToolCalls in the message when present', () => {
    const toolCall = { id: 'tc1', type: 'function' };
    acc.allToolCalls = [toolCall];
    handleStreamEvent(
      { type: 'content', accumulated: 'text' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    const msgs = applySetMessages(dispatchers);
    const msg = msgs[0] as { tool_calls: unknown[] };
    expect(msg.tool_calls).toEqual([toolCall]);
  });

  it('returns a non-zero timestamp', () => {
    const result = handleStreamEvent(
      { type: 'content', accumulated: 'hi' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(result).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// thinking_delta
// ---------------------------------------------------------------------------

describe('handleStreamEvent — thinking_delta', () => {
  it('sets accumulatedThinking from data.thinking', () => {
    handleStreamEvent(
      { type: 'thinking_delta', thinking: 'my thoughts' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(acc.accumulatedThinking).toBe('my thoughts');
  });

  it('calls setMessages with an updater that sets the thinking field', () => {
    handleStreamEvent(
      { type: 'thinking_delta', thinking: 'reasoning here' } as unknown as Record<
        string,
        unknown
      >,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(dispatchers.setMessages).toHaveBeenCalledOnce();
    const msgs = applySetMessages(dispatchers);
    expect((msgs[0] as { thinking: string }).thinking).toBe('reasoning here');
  });

  it('updates the last assistant message thinking when one exists', () => {
    handleStreamEvent(
      { type: 'thinking_delta', thinking: 'new thought' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    const prev = [{ role: 'assistant', content: 'text', thinking: 'old' }];
    const msgs = applySetMessagesWithPrev(dispatchers, prev);
    expect((msgs[0] as { thinking: string }).thinking).toBe('new thought');
  });

  it('returns a non-zero timestamp', () => {
    const result = handleStreamEvent(
      { type: 'thinking_delta', thinking: 't' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(result).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// tool_calls
// ---------------------------------------------------------------------------

describe('handleStreamEvent — tool_calls', () => {
  it('appends new tool calls to acc.allToolCalls', () => {
    const tc1 = { id: 'a' };
    const tc2 = { id: 'b' };
    acc.allToolCalls = [tc1];
    handleStreamEvent(
      { type: 'tool_calls', tool_calls: [tc2] } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(acc.allToolCalls).toEqual([tc1, tc2]);
  });

  it('calls setMessages to update the last assistant message', () => {
    handleStreamEvent(
      { type: 'tool_calls', tool_calls: [{ id: 'x' }] } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(dispatchers.setMessages).toHaveBeenCalledOnce();
  });

  it('includes cached_tool_calls in the update when allCachedToolCalls is non-empty', () => {
    const cached = { id: 'cached1' };
    acc.allCachedToolCalls = [cached];
    handleStreamEvent(
      { type: 'tool_calls', tool_calls: [{ id: 'new1' }] } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    const prev = [{ role: 'assistant', content: '' }];
    const msgs = applySetMessagesWithPrev(dispatchers, prev);
    expect((msgs[0] as { cached_tool_calls: unknown[] }).cached_tool_calls).toEqual([cached]);
  });

  it('returns a non-zero timestamp', () => {
    const result = handleStreamEvent(
      { type: 'tool_calls', tool_calls: [] } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(result).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// cached_tool_calls
// ---------------------------------------------------------------------------

describe('handleStreamEvent — cached_tool_calls', () => {
  it('appends to acc.allCachedToolCalls', () => {
    const c1 = { id: 'c1' };
    handleStreamEvent(
      { type: 'cached_tool_calls', tool_calls: [c1] } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(acc.allCachedToolCalls).toEqual([c1]);
  });

  it('calls setMessages to update the last assistant message with cached_tool_calls', () => {
    handleStreamEvent(
      { type: 'cached_tool_calls', tool_calls: [{ id: 'c2' }] } as unknown as Record<
        string,
        unknown
      >,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(dispatchers.setMessages).toHaveBeenCalledOnce();
    const prev = [{ role: 'assistant', content: '' }];
    const msgs = applySetMessagesWithPrev(dispatchers, prev);
    expect((msgs[0] as { cached_tool_calls: unknown[] }).cached_tool_calls).toContainEqual({
      id: 'c2',
    });
  });

  it('returns a non-zero timestamp', () => {
    const result = handleStreamEvent(
      { type: 'cached_tool_calls', tool_calls: [] } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(result).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// tool_result
// ---------------------------------------------------------------------------

describe('handleStreamEvent — tool_result', () => {
  it('appends a tool message to the message list', () => {
    handleStreamEvent(
      {
        type: 'tool_result',
        tool: 'search',
        result: { data: 'found' },
        tool_call_id: 'tc-1',
      } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    // Two updates: clear running_tool on the assistant, then append the
    // tool message (call index 1).
    expect(dispatchers.setMessages).toHaveBeenCalledTimes(2);
    const mock = dispatchers.setMessages as ReturnType<typeof vi.fn>;
    const updater = mock.mock.calls[1][0];
    const msgs = (typeof updater === 'function' ? updater([]) : updater) as unknown[];
    const msg = msgs[0] as { role: string; name: string; content: string; tool_call_id: string };
    expect(msg.role).toBe('tool');
    expect(msg.name).toBe('search');
    expect(msg.tool_call_id).toBe('tc-1');
    expect(JSON.parse(msg.content)).toEqual({ data: 'found' });
  });

  it('records tool timing when duration_ms is provided', () => {
    handleStreamEvent(
      {
        type: 'tool_result',
        tool: 'fetch',
        result: {},
        tool_call_id: 'tc-2',
        duration_ms: 150,
      } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(acc.toolTimings).toHaveLength(1);
    expect(acc.toolTimings[0]).toMatchObject({ name: 'fetch', duration_ms: 150, tool_call_id: 'tc-2' });
  });

  it('does NOT record timing when duration_ms is absent', () => {
    handleStreamEvent(
      {
        type: 'tool_result',
        tool: 'noop',
        result: null,
      } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(acc.toolTimings).toHaveLength(0);
  });

  it('returns a non-zero timestamp', () => {
    const result = handleStreamEvent(
      {
        type: 'tool_result',
        tool: 't',
        result: {},
      } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(result).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// timing_update
// ---------------------------------------------------------------------------

describe('handleStreamEvent — timing_update', () => {
  it('merges event data into acc.streamingTiming', () => {
    handleStreamEvent(
      { type: 'timing_update', total_ms: 500 } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect((acc.streamingTiming as { total_ms: number }).total_ms).toBe(500);
  });

  it('calls setMessages to update extra_metadata.streaming_timing on the last assistant msg', () => {
    handleStreamEvent(
      { type: 'timing_update', total_ms: 300 } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(dispatchers.setMessages).toHaveBeenCalledOnce();
    const prev = [{ role: 'assistant', content: '', extra_metadata: {} }];
    const msgs = applySetMessagesWithPrev(dispatchers, prev);
    const meta = (msgs[0] as { extra_metadata: { streaming_timing: unknown } }).extra_metadata;
    expect((meta.streaming_timing as { total_ms: number }).total_ms).toBe(300);
  });

  it('returns a non-zero timestamp', () => {
    const result = handleStreamEvent(
      { type: 'timing_update' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(result).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// tool_approval_required
// ---------------------------------------------------------------------------

describe('handleStreamEvent — tool_approval_required', () => {
  it('calls setPendingApproval with the correct PendingToolApproval shape', () => {
    handleStreamEvent(
      {
        type: 'tool_approval_required',
        tool_call_id: 'tca-1',
        tool_name: 'do_thing',
        arguments: { x: 1 },
        iteration: 2,
      } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(dispatchers.setPendingApproval).toHaveBeenCalledWith(
      expect.objectContaining({
        tool_call_id: 'tca-1',
        tool_name: 'do_thing',
        arguments: { x: 1 },
        iteration: 2,
      }),
    );
  });

  it('logs an error and returns early when tool_call_id is missing', () => {
    handleStreamEvent(
      {
        type: 'tool_approval_required',
        tool_name: 'bad_tool',
      } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(logger.error).toHaveBeenCalled();
    expect(dispatchers.setPendingApproval).not.toHaveBeenCalled();
  });

  it('defaults iteration to 0 when not a number', () => {
    handleStreamEvent(
      {
        type: 'tool_approval_required',
        tool_call_id: 'tca-2',
        tool_name: 'tool',
        iteration: 'not-a-number',
      } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(dispatchers.setPendingApproval).toHaveBeenCalledWith(
      expect.objectContaining({ iteration: 0 }),
    );
  });

  it('returns a non-zero timestamp on success', () => {
    const result = handleStreamEvent(
      {
        type: 'tool_approval_required',
        tool_call_id: 'tca-3',
        tool_name: 'tool',
      } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(result).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// tool_rejected
// ---------------------------------------------------------------------------

describe('handleStreamEvent — tool_rejected', () => {
  it('appends a tool message with rejected content', () => {
    handleStreamEvent(
      {
        type: 'tool_rejected',
        tool_call_id: 'tcr-1',
        tool_name: 'risky_tool',
        decision: 'reject',
      } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(dispatchers.setMessages).toHaveBeenCalledOnce();
    const mock = dispatchers.setMessages as ReturnType<typeof vi.fn>;
    const updater = mock.mock.calls[0][0];
    const msgs = (typeof updater === 'function' ? updater([]) : updater) as unknown[];
    const msg = msgs[0] as { role: string; name: string; content: string };
    expect(msg.role).toBe('tool');
    expect(msg.name).toBe('risky_tool');
    expect(JSON.parse(msg.content)).toMatchObject({ rejected: true, decision: 'reject' });
  });

  it('clears the pending approval when tool_call_id matches', () => {
    handleStreamEvent(
      {
        type: 'tool_rejected',
        tool_call_id: 'tcr-1',
        tool_name: 'risky_tool',
        decision: 'reject',
      } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    // setPendingApproval receives an updater function
    const mock = dispatchers.setPendingApproval as ReturnType<typeof vi.fn>;
    const updater = mock.mock.calls[0][0];
    // If prev matches the call id, it should return null
    const resultMatch = (typeof updater === 'function')
      ? updater({ tool_call_id: 'tcr-1' })
      : updater;
    expect(resultMatch).toBeNull();
    // If prev doesn't match, it should return prev
    const otherApproval = { tool_call_id: 'other' };
    const resultNoMatch = (typeof updater === 'function')
      ? updater(otherApproval)
      : updater;
    expect(resultNoMatch).toBe(otherApproval);
  });

  it('handles timeout decision with appropriate message', () => {
    handleStreamEvent(
      {
        type: 'tool_rejected',
        tool_call_id: 'tcr-2',
        tool_name: 'slow_tool',
        decision: 'timeout',
      } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    const mock = dispatchers.setMessages as ReturnType<typeof vi.fn>;
    const updater = mock.mock.calls[0][0];
    const msgs = (typeof updater === 'function' ? updater([]) : updater) as unknown[];
    const msg = msgs[0] as { content: string };
    const parsed = JSON.parse(msg.content) as { reason: string };
    expect(parsed.reason).toContain('timed out');
  });

  it('returns a non-zero timestamp', () => {
    const result = handleStreamEvent(
      {
        type: 'tool_rejected',
        tool_call_id: 'tcr-3',
        tool_name: 'tool',
        decision: 'reject',
      } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(result).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// error
// ---------------------------------------------------------------------------

describe('handleStreamEvent — error', () => {
  it('calls setError with the structured error shape', () => {
    handleStreamEvent(
      {
        type: 'error',
        error: 'Something went wrong',
        error_code: 'RATE_LIMIT',
        error_details: { is_retryable: true },
      } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(dispatchers.setError).toHaveBeenCalledWith({
      message: 'Something went wrong',
      code: 'RATE_LIMIT',
      details: { is_retryable: true },
    });
  });

  it('defaults message and code when fields are missing', () => {
    handleStreamEvent(
      { type: 'error' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(dispatchers.setError).toHaveBeenCalledWith(
      expect.objectContaining({
        message: 'Failed to get response',
        code: 'UNKNOWN_ERROR',
      }),
    );
  });

  it('calls setLoading(false) and setIsStreamingActive(false)', () => {
    handleStreamEvent(
      { type: 'error', error: 'oops' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(dispatchers.setLoading).toHaveBeenCalledWith(false);
    expect(dispatchers.setIsStreamingActive).toHaveBeenCalledWith(false);
  });

  it('returns 0', () => {
    const result = handleStreamEvent(
      { type: 'error' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(result).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// warning
// ---------------------------------------------------------------------------

describe('handleStreamEvent — warning', () => {
  it('appends the warning to the last assistant message', () => {
    handleStreamEvent(
      {
        type: 'warning',
        kind: 'context_overflow',
        message: 'The conversation filled the model context window.',
      } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    const result = applySetMessagesWithPrev(dispatchers, [
      { role: 'assistant', content: 'partial answer' },
    ]) as Array<{ warnings?: Array<{ kind: string; message: string }> }>;
    expect(result[0].warnings).toEqual([
      { kind: 'context_overflow', message: 'The conversation filled the model context window.' },
    ]);
  });

  it('deduplicates identical warnings', () => {
    const event = {
      type: 'warning',
      kind: 'output_truncated',
      message: 'The answer was cut off.',
    } as unknown as Record<string, unknown>;
    handleStreamEvent(event, acc, dispatchers, 'c1', false, doneCallbacks);
    const prev = [
      {
        role: 'assistant',
        content: 'x',
        warnings: [{ kind: 'output_truncated', message: 'The answer was cut off.' }],
      },
    ];
    const result = applySetMessagesWithPrev(dispatchers, prev) as Array<{
      warnings?: unknown[];
    }>;
    expect(result[0].warnings).toHaveLength(1);
  });

  it('creates a fallback assistant message when none exists', () => {
    handleStreamEvent(
      {
        type: 'warning',
        kind: 'context_overflow',
        message: 'overflow',
      } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    const result = applySetMessages(dispatchers) as Array<{
      role: string;
      warnings?: Array<{ kind: string }>;
    }>;
    expect(result).toHaveLength(1);
    expect(result[0].role).toBe('assistant');
    expect(result[0].warnings?.[0].kind).toBe('context_overflow');
  });

  it('defaults kind and message when fields are missing', () => {
    handleStreamEvent(
      { type: 'warning' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    const result = applySetMessages(dispatchers) as Array<{
      warnings?: Array<{ kind: string; message: string }>;
    }>;
    expect(result[0].warnings?.[0].kind).toBe('generic');
    expect(result[0].warnings?.[0].message).toContain('problem');
  });

  it('done event merges warnings into the message and extra_metadata', () => {
    handleStreamEvent(
      {
        type: 'done',
        content: 'final',
        warnings: [{ kind: 'output_truncated', message: 'cut off' }],
      } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    const result = applySetMessagesWithPrev(dispatchers, [
      { role: 'assistant', content: 'final' },
    ]) as Array<{
      warnings?: Array<{ kind: string }>;
      extra_metadata?: { warnings?: Array<{ kind: string }> };
    }>;
    expect(result[0].warnings?.[0].kind).toBe('output_truncated');
    expect(result[0].extra_metadata?.warnings?.[0].kind).toBe('output_truncated');
  });
});

// ---------------------------------------------------------------------------
// context_info
// ---------------------------------------------------------------------------

describe('handleStreamEvent — context_info', () => {
  it('calls setContextInfo with the raw event data cast as ContextInfo', () => {
    const contextData = {
      type: 'context_info',
      total_messages: 10,
      messages_in_context: 5,
      first_in_context_index: 2,
      tokens_used: 1000,
      tokens_available: 4000,
      context_window: 8000,
      provider: 'openai',
      model: 'gpt-4',
    };
    handleStreamEvent(
      contextData as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(dispatchers.setContextInfo).toHaveBeenCalledWith(
      expect.objectContaining({ total_messages: 10, provider: 'openai' }),
    );
  });

  it('returns 0', () => {
    const result = handleStreamEvent(
      { type: 'context_info', total_messages: 1 } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(result).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// done
// ---------------------------------------------------------------------------

describe('handleStreamEvent — done', () => {
  it('sets acc.isDone = true', () => {
    handleStreamEvent(
      { type: 'done' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'chat-1',
      false,
      doneCallbacks,
    );
    expect(acc.isDone).toBe(true);
  });

  it('calls setLoading(false) and setIsStreamingActive(false)', () => {
    handleStreamEvent(
      { type: 'done' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'chat-1',
      false,
      doneCallbacks,
    );
    expect(dispatchers.setLoading).toHaveBeenCalledWith(false);
    expect(dispatchers.setIsStreamingActive).toHaveBeenCalledWith(false);
  });

  it('calls doneCallbacks.onDone with chatId and wasNewChat', () => {
    handleStreamEvent(
      { type: 'done' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'chat-42',
      true,
      doneCallbacks,
    );
    expect(doneCallbacks.onDone).toHaveBeenCalledWith('chat-42', true);
  });

  it('returns 0', () => {
    const result = handleStreamEvent(
      { type: 'done' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'chat-1',
      false,
      doneCallbacks,
    );
    expect(result).toBe(0);
  });

  it('does not call doneCallbacks.onDone a second time if already done', () => {
    acc.isDone = true;
    handleStreamEvent(
      { type: 'done' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'chat-1',
      false,
      doneCallbacks,
    );
    expect(doneCallbacks.onDone).not.toHaveBeenCalled();
  });

  it('calls setMessages with updater that finalizes content from normalizedContent', () => {
    handleStreamEvent(
      { type: 'done', content: 'final normalized [[cite:1]]' } as unknown as Record<
        string,
        unknown
      >,
      acc,
      dispatchers,
      'chat-1',
      false,
      doneCallbacks,
    );
    const prev = [{ role: 'assistant', content: 'streaming...' }];
    const mock = dispatchers.setMessages as ReturnType<typeof vi.fn>;
    // First setMessages call is for the final update
    const updater = mock.mock.calls[0][0];
    const msgs = (typeof updater === 'function' ? updater(prev) : updater) as unknown[];
    expect((msgs[0] as { content: string }).content).toBe('final normalized [[cite:1]]');
  });

  it('includes referenced_entities in the final message when provided', () => {
    const entities = { e1: { id: 'e1', type: 'node', label: 'Entity 1' } };
    handleStreamEvent(
      {
        type: 'done',
        referenced_entities: entities,
      } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'chat-1',
      false,
      doneCallbacks,
    );
    const prev = [{ role: 'assistant', content: 'text' }];
    const mock = dispatchers.setMessages as ReturnType<typeof vi.fn>;
    const updater = mock.mock.calls[0][0];
    const msgs = (typeof updater === 'function' ? updater(prev) : updater) as unknown[];
    expect((msgs[0] as { referenced_entities: unknown }).referenced_entities).toEqual(entities);
  });

  it('merges chunk_citations with validation per_citation verdicts', () => {
    const citations = {
      chunk1: {
        chunk_id: 'chunk1',
        sentence_refs: 'S1',
        label: 'doc.pdf',
        validation_verdict: null,
      },
    };
    const validation = {
      verdict: 'correct' as const,
      reason: 'all good',
      per_citation: { chunk1: { verdict: 'correct', reason: 'matches' } },
    };
    handleStreamEvent(
      {
        type: 'done',
        chunk_citations: citations,
        validation,
      } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'chat-1',
      false,
      doneCallbacks,
    );
    const prev = [{ role: 'assistant', content: 'text' }];
    const mock = dispatchers.setMessages as ReturnType<typeof vi.fn>;
    const updater = mock.mock.calls[0][0];
    const msgs = (typeof updater === 'function' ? updater(prev) : updater) as unknown[];
    const chunkCitations = (msgs[0] as { chunk_citations: Record<string, { validation_verdict: string }> }).chunk_citations;
    expect(chunkCitations.chunk1.validation_verdict).toBe('correct');
  });

  it('logs a warning (no fake apology) when a contentless done arrives', () => {
    // Contentless done = subscribe race: the bridge synthesizes done with no
    // content. The persisted answer arrives via the next poll, so the bubble
    // must NOT be overwritten with an apology (2026-06-10 audit).
    handleStreamEvent(
      { type: 'done' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'chat-1',
      false,
      doneCallbacks,
    );
    expect(logger.warn).toHaveBeenCalledWith('done_without_content_waiting_for_poll');
    const mock = dispatchers.setMessages as ReturnType<typeof vi.fn>;
    for (const call of mock.mock.calls) {
      const updater = call[0];
      const msgs = (typeof updater === 'function'
        ? updater([{ role: 'assistant', content: '' }])
        : updater) as unknown[];
      const msg = msgs[0] as { content?: string };
      expect(msg.content ?? '').not.toContain('apologize');
    }
  });

  it('uses acc.accumulatedThinking as finalThinking when present', () => {
    acc.accumulatedThinking = 'my deep thought';
    acc.currentPhaseContent = 'some content';
    handleStreamEvent(
      { type: 'done' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'chat-1',
      false,
      doneCallbacks,
    );
    const prev = [{ role: 'assistant', content: '' }];
    const mock = dispatchers.setMessages as ReturnType<typeof vi.fn>;
    const updater = mock.mock.calls[0][0];
    const msgs = (typeof updater === 'function' ? updater(prev) : updater) as unknown[];
    expect((msgs[0] as { thinking: string }).thinking).toBe('my deep thought');
  });
});

// ---------------------------------------------------------------------------
// Sequence: multiple deltas → done finalizes
// ---------------------------------------------------------------------------

describe('handleStreamEvent — multi-event sequence', () => {
  it('accumulates content across multiple content events', () => {
    handleStreamEvent(
      { type: 'content', accumulated: 'Hello' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    handleStreamEvent(
      { type: 'content', accumulated: 'Hello world' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(acc.currentPhaseContent).toBe('Hello world');
  });

  it('accumulates tool calls across multiple tool_calls events', () => {
    handleStreamEvent(
      { type: 'tool_calls', tool_calls: [{ id: '1' }] } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    handleStreamEvent(
      { type: 'tool_calls', tool_calls: [{ id: '2' }] } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(acc.allToolCalls).toHaveLength(2);
    expect(acc.allToolCalls).toEqual([{ id: '1' }, { id: '2' }]);
  });

  it('pushes phase content on iteration_progress then combines in content event', () => {
    acc.currentPhaseContent = 'phase 1 content';
    handleStreamEvent(
      { type: 'iteration_progress' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    handleStreamEvent(
      { type: 'content', accumulated: 'phase 2 content' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'c1',
      false,
      doneCallbacks,
    );
    expect(acc.iterationContents).toEqual(['phase 1 content']);
    expect(acc.currentPhaseContent).toBe('phase 2 content');
    const mock = dispatchers.setMessages as ReturnType<typeof vi.fn>;
    const contentCall = mock.mock.calls[mock.mock.calls.length - 1][0];
    const result = (typeof contentCall === 'function' ? contentCall([]) : contentCall) as unknown[];
    expect((result[0] as { content: string }).content).toContain('phase 1 content');
    expect((result[0] as { content: string }).content).toContain('phase 2 content');
  });

  it('done event fires after content events and finalizes the message', () => {
    acc.currentPhaseContent = 'full response text';
    handleStreamEvent(
      { type: 'done', content: 'normalized full response' } as unknown as Record<string, unknown>,
      acc,
      dispatchers,
      'chat-final',
      false,
      doneCallbacks,
    );
    expect(acc.isDone).toBe(true);
    expect(doneCallbacks.onDone).toHaveBeenCalledWith('chat-final', false);
    expect(dispatchers.setLoading).toHaveBeenCalledWith(false);
  });
});

// ---------------------------------------------------------------------------
// Phase 0 fixes (2026-06-10 audit): tool_start status, phantom thinking
// placeholder, contentless-done apology guard
// ---------------------------------------------------------------------------

describe('tool_start running status', () => {
  it('sets running_tool on the assistant message', () => {
    handleStreamEvent(
      { type: 'tool_start', tool: 'search_nodes' },
      acc, dispatchers, 'chat-1', false, doneCallbacks,
    );
    const msgs = applySetMessagesWithPrev(dispatchers, [
      { role: 'assistant', content: '' },
    ]);
    expect((msgs[0] as { running_tool?: string }).running_tool).toBe('search_nodes');
  });

  it('clears running_tool when the tool result arrives', () => {
    handleStreamEvent(
      { type: 'tool_result', tool: 'search_nodes', result: {}, tool_call_id: 't1' },
      acc, dispatchers, 'chat-1', false, doneCallbacks,
    );
    const msgs = applySetMessagesWithPrev(dispatchers, [
      { role: 'assistant', content: '', running_tool: 'search_nodes' },
    ]);
    expect((msgs[0] as { running_tool?: string }).running_tool).toBeUndefined();
  });
});

describe('done event cleanup (phase 0)', () => {
  it('clears the optimistic "..." thinking placeholder when no thinking arrived', () => {
    handleStreamEvent(
      { type: 'done', content: 'Answer.' },
      acc, dispatchers, 'chat-1', false, doneCallbacks,
    );
    const msgs = applySetMessagesWithPrev(dispatchers, [
      { role: 'assistant', content: 'Answer.', thinking: '...' },
    ]);
    expect((msgs[0] as { thinking?: string }).thinking).toBeUndefined();
  });

  it('keeps real thinking past done', () => {
    handleStreamEvent(
      { type: 'done', content: 'Answer.' },
      acc, dispatchers, 'chat-1', false, doneCallbacks,
    );
    const msgs = applySetMessagesWithPrev(dispatchers, [
      { role: 'assistant', content: 'Answer.', thinking: 'real reasoning' },
    ]);
    expect((msgs[0] as { thinking?: string }).thinking).toBe('real reasoning');
  });

  it('does not overwrite the bubble with an apology on a contentless done', () => {
    handleStreamEvent(
      { type: 'done' },
      acc, dispatchers, 'chat-1', false, doneCallbacks,
    );
    const mock = dispatchers.setMessages as ReturnType<typeof vi.fn>;
    for (let i = 0; i < mock.mock.calls.length; i++) {
      const msgs = applySetMessagesWithPrev(
        dispatchers,
        [{ role: 'assistant', content: '' }],
        i,
      );
      const content = (msgs[0] as { content?: string })?.content ?? '';
      expect(content).not.toContain('I apologize');
    }
    expect(logger.warn).toHaveBeenCalledWith('done_without_content_waiting_for_poll');
  });
});
