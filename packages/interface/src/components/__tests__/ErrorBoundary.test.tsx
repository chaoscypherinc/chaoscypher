// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Regression tests for the F13 ErrorBoundary fallback.
 *
 * Default render must show a generic user-facing message and a short
 * reference ID — and NOT the raw `error.message` (which can contain
 * file paths or internal symbols). Clicking "Show details" reveals
 * the raw message inside a collapsible block for operator debugging.
 */

import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ErrorBoundary } from '../ErrorBoundary';

function Boom(): null {
  throw new Error('internal/path/leaked.ts:42 — implementation detail');
}

describe('ErrorBoundary', () => {
  // React logs the caught error to console.error in development; silence it
  // here so the test output is clean.
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    consoleErrorSpy.mockRestore();
  });

  it('shows the generic message and reference ID; hides the raw error message', () => {
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );

    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    expect(screen.getByText(/Reference: err-/)).toBeInTheDocument();
    expect(
      screen.queryByText(/internal\/path\/leaked\.ts/),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /show details/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /try again/i }),
    ).toBeInTheDocument();
  });

  it('reveals the raw error message after clicking "Show details"', () => {
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );

    fireEvent.click(screen.getByRole('button', { name: /show details/i }));

    expect(screen.getByTestId('error-boundary-details')).toHaveTextContent(
      'internal/path/leaked.ts:42',
    );
    expect(
      screen.getByRole('button', { name: /hide details/i }),
    ).toBeInTheDocument();
  });

  it('renders the provided custom fallback when one is given', () => {
    render(
      <ErrorBoundary fallback={<div>Custom fallback</div>}>
        <Boom />
      </ErrorBoundary>,
    );

    expect(screen.getByText('Custom fallback')).toBeInTheDocument();
    expect(screen.queryByText('Something went wrong')).not.toBeInTheDocument();
  });
});
