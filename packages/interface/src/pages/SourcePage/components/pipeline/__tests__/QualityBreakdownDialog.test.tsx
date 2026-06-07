// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { QualityBreakdownDialog } from '../QualityBreakdownDialog';

describe('QualityBreakdownDialog', () => {
  it('renders nothing when closed', () => {
    render(
      <QualityBreakdownDialog
        open={false}
        score={null}
        loading={false}
        onClose={vi.fn()}
        onRecalculate={vi.fn()}
      />,
    );
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('renders dialog when open', () => {
    render(
      <QualityBreakdownDialog
        open={true}
        score={null}
        loading={false}
        onClose={vi.fn()}
        onRecalculate={vi.fn()}
      />,
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('fires onClose when escape is pressed', () => {
    const onClose = vi.fn();
    render(
      <QualityBreakdownDialog
        open={true}
        score={null}
        loading={false}
        onClose={onClose}
        onRecalculate={vi.fn()}
      />,
    );
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape', code: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });
});
