// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { FunnelPill } from '../FunnelPill';

describe('FunnelPill', () => {
  const baseProps = {
    count: '142k',
    label: 'LOAD',
    sublabel: 'chars',
    severity: 'neutral' as const,
    selected: false,
    onClick: vi.fn(),
  };

  it('renders count, label, sublabel', () => {
    render(<FunnelPill {...baseProps} />);
    expect(screen.getByText('142k')).toBeInTheDocument();
    expect(screen.getByText('LOAD')).toBeInTheDocument();
    expect(screen.getByText('chars')).toBeInTheDocument();
  });

  it('fires onClick when clicked', async () => {
    const onClick = vi.fn();
    render(<FunnelPill {...baseProps} onClick={onClick} />);
    await userEvent.click(screen.getByRole('button', { name: /load/i }));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('applies selected styling via data attribute', () => {
    const { rerender } = render(<FunnelPill {...baseProps} selected={false} />);
    expect(screen.getByRole('button')).toHaveAttribute('data-selected', 'false');
    rerender(<FunnelPill {...baseProps} selected={true} />);
    expect(screen.getByRole('button')).toHaveAttribute('data-selected', 'true');
  });

  it('applies severity via data attribute for visual regression hooks', () => {
    render(<FunnelPill {...baseProps} severity="err" />);
    expect(screen.getByRole('button')).toHaveAttribute('data-severity', 'err');
  });

  it('renders as a non-button when interactive=false', () => {
    render(<FunnelPill {...baseProps} interactive={false} />);
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByText('LOAD')).toBeInTheDocument();
  });
});
