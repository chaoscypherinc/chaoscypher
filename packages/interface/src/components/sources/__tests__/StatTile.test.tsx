// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { StatTile } from '../StatTile';

describe('StatTile', () => {
  it('renders value and label', () => {
    render(<StatTile value={487} label="Entities" color="#7fb4ff" icon={<span />} />);
    expect(screen.getByText('487')).toBeInTheDocument();
    expect(screen.getByText('Entities')).toBeInTheDocument();
  });

  it('is not a button when no onClick', () => {
    render(<StatTile value={1} label="X" color="#fff" icon={<span />} />);
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('is an accessible button with aria-label when clickable', async () => {
    const onClick = vi.fn();
    render(
      <StatTile value={487} label="Entities" color="#7fb4ff" icon={<span />} onClick={onClick} ariaLabel="Entities: 487, open Extraction tab" />,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Entities: 487, open Extraction tab' }));
    expect(onClick).toHaveBeenCalledOnce();
  });
});
