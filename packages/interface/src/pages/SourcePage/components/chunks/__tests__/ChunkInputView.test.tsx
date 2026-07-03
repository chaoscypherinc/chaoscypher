// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { ChunkInputView } from '../ChunkInputView';

describe('ChunkInputView (controlled by tab-level showRemoved prop)', () => {
  it('renders cleaned content by default (showRemoved=false)', () => {
    render(
      <ChunkInputView cleaned="The TLR4 protein..." rawContent={null} showRemoved={false} />,
    );
    expect(screen.getByText(/TLR4 protein/)).toBeInTheDocument();
    expect(screen.queryByTestId('chunk-input-diff')).toBeNull();
  });

  it('ignores showRemoved=true when rawContent is null (legacy source)', () => {
    render(<ChunkInputView cleaned="cleaned text" rawContent={null} showRemoved={true} />);
    expect(screen.getByText(/cleaned text/)).toBeInTheDocument();
    expect(screen.queryByTestId('chunk-input-diff')).toBeNull();
  });

  it('renders diff overlay when showRemoved=true AND raw is available', () => {
    render(<ChunkInputView cleaned="hello" rawContent="hello world" showRemoved={true} />);
    expect(screen.getByTestId('chunk-input-diff')).toBeInTheDocument();
  });
});

describe('ChunkInputView — citation sentence highlight', () => {
  // "First sentence here." is chars 0–20; "Second sentence here." is 21–42.
  const CLEANED = 'First sentence here. Second sentence here.';
  const META = { sentence_offsets: [{ start: 0, end: 20 }, { start: 21, end: 42 }] };

  it('marks the cited sentence when highlightSentRef + offsets are given', () => {
    const { container } = render(
      <ChunkInputView
        cleaned={CLEANED}
        rawContent={null}
        showRemoved={false}
        highlightSentRef="S1"
        chunkMetadata={META}
      />,
    );
    const marks = container.querySelectorAll('mark');
    expect(marks.length).toBe(1);
    expect(marks[0].textContent).toContain('First sentence here.');
  });

  it('marks the second sentence for S2', () => {
    const { container } = render(
      <ChunkInputView
        cleaned={CLEANED}
        rawContent={null}
        showRemoved={false}
        highlightSentRef="S2"
        chunkMetadata={META}
      />,
    );
    const marks = container.querySelectorAll('mark');
    expect(marks.length).toBe(1);
    expect(marks[0].textContent).toContain('Second sentence here.');
  });

  it('renders plain (no mark) without a highlightSentRef', () => {
    const { container } = render(
      <ChunkInputView cleaned={CLEANED} rawContent={null} showRemoved={false} />,
    );
    expect(container.querySelectorAll('mark').length).toBe(0);
  });
});
