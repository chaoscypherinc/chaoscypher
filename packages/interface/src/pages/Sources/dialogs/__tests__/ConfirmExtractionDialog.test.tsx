// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { makeWrapper } from '../../../../test/renderWithProviders';
import { ConfirmExtractionDialog } from '../ConfirmExtractionDialog';
import type { UnifiedSource } from '../../../../types';
import type { ExtractionDomain } from '../../../../services/api/sourceProcessing';

const DOMAINS: ExtractionDomain[] = [
  { name: 'science', description: 'Science', builtin: true },
  { name: 'general', description: 'General', builtin: true },
  { name: 'legal', description: 'Legal', builtin: true },
  { name: 'medical', description: 'Medical', builtin: true },
  { name: 'generic', description: 'Generic catch-all', builtin: true },
];

function makeSource(overrides: Partial<UnifiedSource> = {}): UnifiedSource {
  return {
    id: 'src-1',
    stage: 'queued',
    title: 'paper.pdf',
    source_type: 'pdf',
    size: 2048,
    status: 'awaiting_confirmation',
    created_at: '2026-05-28T00:00:00Z',
    confirmation_required: true,
    detection_confidence: 0.82,
    detection_ranking: [
      { domain: 'science', score: 4.2 },
      { domain: 'general', score: 1.6 },
      { domain: 'legal', score: 1.1 },
      { domain: 'medical', score: 0.4 },
    ],
    proposed_extraction_options: { analysis_depth: 'full', filtering_mode: 'balanced' },
    ...overrides,
  };
}

describe('<ConfirmExtractionDialog />', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows the top-3 ranked domains with score chips, best pre-selected', () => {
    render(
      <ConfirmExtractionDialog
        open
        source={makeSource()}
        availableDomains={DOMAINS}
        submitting={false}
        onClose={vi.fn()}
        onConfirm={vi.fn()}
      />,
      { wrapper: makeWrapper() },
    );

    // The dialog title references confirmation.
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    // The recommended-domain list shows the top 3 with their scores.
    expect(screen.getByText('science')).toBeInTheDocument();
    expect(screen.getByText('general')).toBeInTheDocument();
    expect(screen.getByText('legal')).toBeInTheDocument();
    // 4th-ranked domain not surfaced as a quick-pick (still in the full dropdown).
    expect(screen.queryByText(/medical/i)).not.toBeInTheDocument();

    // Score chips for the top picks.
    expect(screen.getByText('4.2')).toBeInTheDocument();
    expect(screen.getByText('1.6')).toBeInTheDocument();

    // Best is pre-selected (the recommended pick is marked).
    const science = screen.getByRole('button', { name: /science/i });
    expect(science).toHaveAttribute('aria-pressed', 'true');
  });

  it("shows the low-confidence prompt when detection wasn't confident", () => {
    render(
      <ConfirmExtractionDialog
        open
        source={makeSource({ detection_low_confidence: true, detection_ranking: [] })}
        availableDomains={DOMAINS}
        submitting={false}
        onClose={vi.fn()}
        onConfirm={vi.fn()}
      />,
      { wrapper: makeWrapper() },
    );
    expect(screen.getByText(/detection wasn't confident/i)).toBeInTheDocument();
  });

  it('defaults to the concrete Generic domain (not "Auto") when detection is not confident', () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmExtractionDialog
        open
        source={makeSource({ detection_low_confidence: true, detection_ranking: [] })}
        availableDomains={DOMAINS}
        submitting={false}
        onClose={vi.fn()}
        onConfirm={onConfirm}
      />,
      { wrapper: makeWrapper() },
    );

    // The selector shows the concrete fallback domain, not the ambiguous "Auto".
    expect(screen.getByRole('combobox', { name: /domain/i })).toHaveTextContent(/generic/i);

    fireEvent.click(screen.getByRole('button', { name: /^confirm$/i }));
    expect(onConfirm.mock.calls[0][0].domain).toBe('generic');
  });

  it('submits the chosen domain + seeded options on confirm', () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmExtractionDialog
        open
        source={makeSource()}
        availableDomains={DOMAINS}
        submitting={false}
        onClose={vi.fn()}
        onConfirm={onConfirm}
      />,
      { wrapper: makeWrapper() },
    );

    // Pick the 2nd-ranked recommendation.
    fireEvent.click(screen.getByRole('button', { name: /general/i }));
    fireEvent.click(screen.getByRole('button', { name: /^confirm$/i }));

    expect(onConfirm).toHaveBeenCalledTimes(1);
    const payload = onConfirm.mock.calls[0][0];
    expect(payload.domain).toBe('general');
    // Seeded from the proposal.
    expect(payload.analysis_depth).toBe('full');
    expect(payload.filtering_mode).toBe('balanced');
  });

  it('falls back to undefined (auto) when no Generic domain is available', () => {
    const onConfirm = vi.fn();
    // Domain list without a "generic" entry — nothing concrete to default to.
    const noGeneric = DOMAINS.filter((d) => d.name !== 'generic');
    render(
      <ConfirmExtractionDialog
        open
        source={makeSource({ detection_low_confidence: true, detection_ranking: [] })}
        availableDomains={noGeneric}
        submitting={false}
        onClose={vi.fn()}
        onConfirm={onConfirm}
      />,
      { wrapper: makeWrapper() },
    );

    fireEvent.click(screen.getByRole('button', { name: /^confirm$/i }));

    expect(onConfirm).toHaveBeenCalledTimes(1);
    // The __auto__ sentinel must map to undefined — never sent as a literal string.
    expect(onConfirm.mock.calls[0][0].domain).toBeUndefined();
  });

  it('disables confirm while submitting', () => {
    render(
      <ConfirmExtractionDialog
        open
        source={makeSource()}
        availableDomains={DOMAINS}
        submitting
        onClose={vi.fn()}
        onConfirm={vi.fn()}
      />,
      { wrapper: makeWrapper() },
    );
    expect(screen.getByRole('button', { name: /^confirm/i })).toBeDisabled();
  });
});
