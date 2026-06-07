// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { ConfirmRerunDialog } from '../ConfirmRerunDialog';

describe('ConfirmRerunDialog', () => {
  it('renders explanatory copy with prior-attempt count', () => {
    render(
      <ConfirmRerunDialog
        open
        chunkIndex={5}
        priorAttemptCount={1}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText(/chunk 6/i)).toBeInTheDocument();
    // Lifecycle wording in the body explainer (matches the b tag)
    expect(screen.getByText(/extracting/i)).toBeInTheDocument();
    expect(screen.getByText(/1 prior attempt is preserved/i)).toBeInTheDocument();
  });

  it('cancel fires onCancel', () => {
    const onCancel = vi.fn();
    render(
      <ConfirmRerunDialog
        open
        chunkIndex={0}
        priorAttemptCount={0}
        onConfirm={() => {}}
        onCancel={onCancel}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it('confirm fires onConfirm', () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmRerunDialog
        open
        chunkIndex={0}
        priorAttemptCount={0}
        onConfirm={onConfirm}
        onCancel={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /rerun chunk/i }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it('not open: nothing rendered', () => {
    render(
      <ConfirmRerunDialog
        open={false}
        chunkIndex={0}
        priorAttemptCount={0}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('shows pending state during mutation', () => {
    render(
      <ConfirmRerunDialog
        open
        chunkIndex={0}
        priorAttemptCount={0}
        pending
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByRole('button', { name: /rerunning/i })).toBeDisabled();
  });
});
