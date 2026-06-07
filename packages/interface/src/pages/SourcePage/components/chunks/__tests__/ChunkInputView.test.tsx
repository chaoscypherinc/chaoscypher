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
