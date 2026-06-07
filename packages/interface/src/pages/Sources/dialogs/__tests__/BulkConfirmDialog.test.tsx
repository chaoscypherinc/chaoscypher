// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { makeWrapper } from '../../../../test/renderWithProviders';
import { BulkConfirmDialog } from '../BulkConfirmDialog';
import type { UnifiedSource } from '../../../../types';

function awaiting(
  id: string,
  title: string,
  domain: string,
  lowConfidence?: boolean,
): UnifiedSource {
  return {
    id,
    stage: 'queued',
    title,
    source_type: 'pdf',
    size: 1024,
    status: 'awaiting_confirmation',
    created_at: '2026-05-28T00:00:00Z',
    confirmation_required: true,
    detection_ranking: [{ domain, score: lowConfidence ? 0.4 : 3.0 }],
    detection_low_confidence: lowConfidence,
  };
}

function awaitingNoRanking(id: string, title: string): UnifiedSource {
  return {
    id,
    stage: 'queued',
    title,
    source_type: 'pdf',
    size: 1024,
    status: 'awaiting_confirmation',
    created_at: '2026-05-28T00:00:00Z',
    confirmation_required: true,
    detection_ranking: [],
    detection_low_confidence: true,
  };
}

const CONFIDENT_SOURCES = [
  awaiting('a', 'alpha.pdf', 'science'),
  awaiting('b', 'beta.pdf', 'legal'),
];

const LOW_CONF_SOURCE = awaiting('c', 'gamma.pdf', 'news', true);
const NO_RANKING_SOURCE = awaitingNoRanking('d', 'delta.pdf');

describe('<BulkConfirmDialog />', () => {
  // ── existing behaviour (confident sources) ──────────────────────────────

  it('lists each parked source with its recommended domain', () => {
    render(
      <BulkConfirmDialog
        open
        sources={CONFIDENT_SOURCES}
        submitting={false}
        errors={[]}
        onClose={vi.fn()}
        onConfirmAll={vi.fn()}
      />,
      { wrapper: makeWrapper() },
    );
    expect(screen.getByText('alpha.pdf')).toBeInTheDocument();
    expect(screen.getByText('beta.pdf')).toBeInTheDocument();
    expect(screen.getByText('science')).toBeInTheDocument();
    expect(screen.getByText('legal')).toBeInTheDocument();
  });

  it('confirms all confident sources with their recommended (ranking[0]) domain', () => {
    const onConfirmAll = vi.fn();
    render(
      <BulkConfirmDialog
        open
        sources={CONFIDENT_SOURCES}
        submitting={false}
        errors={[]}
        onClose={vi.fn()}
        onConfirmAll={onConfirmAll}
      />,
      { wrapper: makeWrapper() },
    );
    fireEvent.click(screen.getByRole('button', { name: /confirm all/i }));
    expect(onConfirmAll).toHaveBeenCalledWith([
      { source_id: 'a', domain: 'science' },
      { source_id: 'b', domain: 'legal' },
    ]);
  });

  it('renders a per-item failure note when an item errored (partial failure, not abort)', () => {
    render(
      <BulkConfirmDialog
        open
        sources={CONFIDENT_SOURCES}
        submitting={false}
        errors={[{ source_id: 'b', error: 'Not awaiting confirmation' }]}
        onClose={vi.fn()}
        onConfirmAll={vi.fn()}
      />,
      { wrapper: makeWrapper() },
    );
    // alpha (no error) still shows; beta carries the error message.
    expect(screen.getByText('alpha.pdf')).toBeInTheDocument();
    expect(screen.getByText(/not awaiting confirmation/i)).toBeInTheDocument();
  });

  // ── low-confidence guard ─────────────────────────────────────────────────

  it('flags low-confidence rows with a warning chip', () => {
    render(
      <BulkConfirmDialog
        open
        sources={[...CONFIDENT_SOURCES, LOW_CONF_SOURCE]}
        submitting={false}
        errors={[]}
        onClose={vi.fn()}
        onConfirmAll={vi.fn()}
      />,
      { wrapper: makeWrapper() },
    );
    expect(screen.getByText('gamma.pdf')).toBeInTheDocument();
    expect(
      screen.getByText(/review individually.*detection wasn't confident/i),
    ).toBeInTheDocument();
  });

  it('excludes low-confidence sources from the onConfirmAll payload (only confident IDs passed)', () => {
    const onConfirmAll = vi.fn();
    render(
      <BulkConfirmDialog
        open
        sources={[...CONFIDENT_SOURCES, LOW_CONF_SOURCE]}
        submitting={false}
        errors={[]}
        onClose={vi.fn()}
        onConfirmAll={onConfirmAll}
      />,
      { wrapper: makeWrapper() },
    );
    fireEvent.click(screen.getByRole('button', { name: /confirm 2 detected/i }));
    expect(onConfirmAll).toHaveBeenCalledWith([
      { source_id: 'a', domain: 'science' },
      { source_id: 'b', domain: 'legal' },
    ]);
    // Ensure the low-confidence source_id is NOT in the payload.
    const [payload] = onConfirmAll.mock.calls[0] as [Array<{ source_id: string }>];
    expect(payload.some((item) => item.source_id === 'c')).toBe(false);
  });

  it('also excludes sources with empty ranking from the payload', () => {
    const onConfirmAll = vi.fn();
    render(
      <BulkConfirmDialog
        open
        sources={[awaiting('a', 'alpha.pdf', 'science'), NO_RANKING_SOURCE]}
        submitting={false}
        errors={[]}
        onClose={vi.fn()}
        onConfirmAll={onConfirmAll}
      />,
      { wrapper: makeWrapper() },
    );
    fireEvent.click(screen.getByRole('button', { name: /confirm 1 detected/i }));
    expect(onConfirmAll).toHaveBeenCalledWith([{ source_id: 'a', domain: 'science' }]);
    const [payload] = onConfirmAll.mock.calls[0] as [Array<{ source_id: string }>];
    expect(payload.some((item) => item.source_id === 'd')).toBe(false);
  });

  it('disables the Confirm-All button when ALL selected sources are low-confidence', () => {
    render(
      <BulkConfirmDialog
        open
        sources={[LOW_CONF_SOURCE, NO_RANKING_SOURCE]}
        submitting={false}
        errors={[]}
        onClose={vi.fn()}
        onConfirmAll={vi.fn()}
      />,
      { wrapper: makeWrapper() },
    );
    const confirmBtn = screen.getByRole('button', { name: /confirm all/i });
    expect(confirmBtn).toBeDisabled();
  });

  it('shows a summary note about how many sources need individual review in a mixed selection', () => {
    render(
      <BulkConfirmDialog
        open
        sources={[...CONFIDENT_SOURCES, LOW_CONF_SOURCE]}
        submitting={false}
        errors={[]}
        onClose={vi.fn()}
        onConfirmAll={vi.fn()}
      />,
      { wrapper: makeWrapper() },
    );
    expect(
      screen.getByText(/1 source.* need individual review and will not be bulk-confirmed/i),
    ).toBeInTheDocument();
  });

  it('shows an all-low-confidence guidance note when every source is low-confidence', () => {
    render(
      <BulkConfirmDialog
        open
        sources={[LOW_CONF_SOURCE, NO_RANKING_SOURCE]}
        submitting={false}
        errors={[]}
        onClose={vi.fn()}
        onConfirmAll={vi.fn()}
      />,
      { wrapper: makeWrapper() },
    );
    expect(
      screen.getByText(/all 2 sources need individual review/i),
    ).toBeInTheDocument();
  });
});
