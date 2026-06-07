// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ConnectionErrorScreen } from '../ConnectionErrorScreen';

describe('ConnectionErrorScreen', () => {
  it('shows the error message, retry button, and docker-logs hint', () => {
    render(<ConnectionErrorScreen error="Backend offline." onRetry={() => {}} />);
    expect(screen.getByText('Connection Error')).toBeInTheDocument();
    expect(screen.getByText('Backend offline.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
    expect(screen.getByText(/docker logs cortex/)).toBeInTheDocument();
  });

  it('invokes the onRetry callback when the Retry button is clicked', () => {
    const onRetry = vi.fn();
    render(<ConnectionErrorScreen error="x" onRetry={onRetry} />);
    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
