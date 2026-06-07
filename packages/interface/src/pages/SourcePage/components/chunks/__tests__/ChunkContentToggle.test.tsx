// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { ChunkContentToggle } from '../ChunkContentToggle';

describe('ChunkContentToggle', () => {
  it('renders INPUT and OUTPUT buttons', () => {
    render(<ChunkContentToggle view="input" outputAvailable={true} onChange={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'INPUT' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'OUTPUT' })).toBeInTheDocument();
  });

  it('disables OUTPUT when not available + shows tooltip text', async () => {
    render(<ChunkContentToggle view="input" outputAvailable={false} onChange={vi.fn()} />);
    const btn = screen.getByRole('button', { name: 'OUTPUT' });
    expect(btn).toBeDisabled();
  });

  it('fires onChange on click', async () => {
    const onChange = vi.fn();
    render(<ChunkContentToggle view="input" outputAvailable={true} onChange={onChange} />);
    await userEvent.click(screen.getByRole('button', { name: 'OUTPUT' }));
    expect(onChange).toHaveBeenCalledWith('output');
  });

  it('does not fire onChange when clicking the active view', async () => {
    const onChange = vi.fn();
    render(<ChunkContentToggle view="input" outputAvailable={true} onChange={onChange} />);
    await userEvent.click(screen.getByRole('button', { name: 'INPUT' }));
    expect(onChange).not.toHaveBeenCalled();
  });
});
