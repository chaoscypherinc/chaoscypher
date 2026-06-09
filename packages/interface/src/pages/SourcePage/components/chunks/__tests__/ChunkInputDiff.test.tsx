// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { render } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { ChunkInputDiff } from '../ChunkInputDiff';

describe('ChunkInputDiff', () => {
  it('renders kept text normally', () => {
    const { container } = render(<ChunkInputDiff cleaned="hello" raw="hello" />);
    expect(container.textContent).toBe('hello');
    expect(container.querySelectorAll('[data-removed="true"]')).toHaveLength(0);
  });

  it('strikes a removed trailing word', () => {
    const { container } = render(<ChunkInputDiff cleaned="hello" raw="hello world" />);
    expect(container.textContent).toContain('hello');
    const removed = Array.from(container.querySelectorAll('[data-removed="true"]'));
    expect(removed.length).toBeGreaterThan(0);
    expect(removed.map((el) => el.textContent).join('')).toContain('world');
    expect(removed[0]).toHaveStyle({ textDecoration: 'line-through' });
  });

  it('strikes a removed word in the middle as one contiguous block', () => {
    const { container } = render(<ChunkInputDiff cleaned="foo baz" raw="foo bar baz" />);
    const removed = Array.from(container.querySelectorAll('[data-removed="true"]'));
    // Word-level diff groups consecutive removed tokens into a single span.
    expect(removed.map((el) => el.textContent).join('').trim()).toBe('bar');
  });

  it('strikes a removed header line cleanly (the bug this fixes)', () => {
    const { container } = render(
      <ChunkInputDiff cleaned="BOOK ONE: 1805" raw="CHAPTER I BOOK ONE: 1805" />,
    );
    const removed = Array.from(container.querySelectorAll('[data-removed="true"]'));
    // No mid-word shredding: "CHAPTER I" comes out as one contiguous strike.
    expect(removed.map((el) => el.textContent).join('').trim()).toBe('CHAPTER I');
    expect(container.textContent).toContain('BOOK ONE: 1805');
  });

  it('does not strike CRLF→LF line-ending differences as removed text', () => {
    // CRLF sources keep \r\n in raw_content while the cleaned text uses \n.
    // Line endings are invisible — flagging them paints red artifacts at
    // every line break without conveying anything.
    const { container } = render(
      <ChunkInputDiff cleaned={'line one\nline two'} raw={'line one\r\nline two'} />,
    );
    expect(container.querySelectorAll('[data-removed="true"]')).toHaveLength(0);
    expect(container.textContent).toContain('line one');
    expect(container.textContent).toContain('line two');
  });
});
