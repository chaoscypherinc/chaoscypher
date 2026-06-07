// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Component tests for `UploadWizard` — the visible wizard steps (Analyzing +
 * inline ConfirmExtractionDialog), driven by a stubbed `useUploadWizard`
 * return value.
 *
 * Covers (spec §5 UI bullet):
 *   - the Analyzing step renders during phase 'analyzing';
 *   - the three review states: top-3 ranking, low-confidence, no_text;
 *   - confirm fires the hook's `confirm` and the Confirm button is disabled
 *     while the confirm mutation is in flight (`confirming`);
 *   - the analyzing → confirm transition (phase change re-renders the right UI).
 */

import { render, screen, fireEvent } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material';
import { describe, it, expect, vi } from 'vitest';

import { UploadWizard } from '../UploadWizard';
import type { UseUploadWizardReturn } from '../../hooks/useUploadWizard';
import type { ExtractionDomain } from '../../services/api/sourceProcessing';
import type { UnifiedSource } from '../../types';

const DOMAINS: ExtractionDomain[] = [
  { name: 'legal', description: 'Legal', builtin: true },
  { name: 'generic', description: 'Generic', builtin: true },
];

function makeReviewSource(overrides: Partial<UnifiedSource> = {}): UnifiedSource {
  return {
    id: 'src-1',
    stage: 'processing',
    title: 'Doc',
    source_type: 'pdf',
    size: 1024,
    status: 'indexing',
    created_at: '2026-05-29T00:00:00Z',
    detection_ranking: [
      { domain: 'legal', score: 8.2 },
      { domain: 'generic', score: 2.1 },
    ],
    detection_low_confidence: false,
    proposed_extraction_options: {},
    ...overrides,
  };
}

function makeWizard(overrides: Partial<UseUploadWizardReturn> = {}): UseUploadWizardReturn {
  return {
    phase: 'idle',
    source: null,
    error: null,
    confirming: false,
    start: vi.fn().mockResolvedValue('wizard'),
    confirm: vi.fn().mockResolvedValue(undefined),
    cancel: vi.fn(),
    ...overrides,
  };
}

function renderWizard(wizard: UseUploadWizardReturn) {
  const theme = createTheme({ palette: { mode: 'dark' } });
  return render(
    <ThemeProvider theme={theme}>
      <UploadWizard wizard={wizard} availableDomains={DOMAINS} />
    </ThemeProvider>,
  );
}

describe('UploadWizard', () => {
  it('renders nothing actionable when idle', () => {
    renderWizard(makeWizard({ phase: 'idle' }));
    expect(screen.queryByText(/Analyzing your document/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Confirm extraction domain/i)).not.toBeInTheDocument();
  });

  // ── Analyzing step ─────────────────────────────────────────────────────

  it('shows the Analyzing step during phase "analyzing"', () => {
    renderWizard(makeWizard({ phase: 'analyzing' }));
    expect(screen.getByText(/Analyzing your document/i)).toBeInTheDocument();
    expect(screen.getByText(/Detecting the best extraction domain/i)).toBeInTheDocument();
  });

  it('cancel from the Analyzing step calls wizard.cancel', () => {
    const wizard = makeWizard({ phase: 'analyzing' });
    renderWizard(wizard);
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(wizard.cancel).toHaveBeenCalledTimes(1);
  });

  // ── Review states ──────────────────────────────────────────────────────

  it('review (top-3): renders the ranked quick-pick row', () => {
    renderWizard(makeWizard({ phase: 'review', source: makeReviewSource() }));
    expect(screen.getByText(/Confirm extraction domain/i)).toBeInTheDocument();
    expect(screen.getByText(/Recommended \(top 2\)/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /legal/i })).toBeInTheDocument();
    // Not the low-confidence / no_text prompts.
    expect(screen.queryByText(/wasn't confident/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Not enough text/i)).not.toBeInTheDocument();
  });

  it('review (low-confidence): shows the "pick a domain" prompt', () => {
    renderWizard(
      makeWizard({
        phase: 'review',
        source: makeReviewSource({
          detection_ranking: [],
          detection_low_confidence: true,
          proposed_extraction_options: { low_confidence: true },
        }),
      }),
    );
    expect(screen.getByText(/wasn't confident/i)).toBeInTheDocument();
    expect(screen.queryByText(/Recommended \(top/i)).not.toBeInTheDocument();
  });

  it('review (no_text): shows the "not enough text" prompt', () => {
    renderWizard(
      makeWizard({
        phase: 'review',
        source: makeReviewSource({
          detection_ranking: [{ domain: 'generic', score: 0 }],
          detection_low_confidence: true,
          proposed_extraction_options: { low_confidence: true, no_text: true },
        }),
      }),
    );
    expect(screen.getByText(/Not enough text to detect/i)).toBeInTheDocument();
    // no_text wins over the generic low-confidence copy.
    expect(screen.queryByText(/wasn't confident/i)).not.toBeInTheDocument();
  });

  // ── Confirm / in-flight ─────────────────────────────────────────────────

  it('confirm fires the hook confirm with the chosen options', () => {
    const wizard = makeWizard({ phase: 'review', source: makeReviewSource() });
    renderWizard(wizard);
    fireEvent.click(screen.getByRole('button', { name: /^confirm$/i }));
    expect(wizard.confirm).toHaveBeenCalledTimes(1);
    // The pre-selected top domain (legal) is submitted.
    expect(wizard.confirm).toHaveBeenCalledWith(
      expect.objectContaining({ domain: 'legal' }),
    );
  });

  it('disables the Confirm button while the confirm mutation is in flight', () => {
    renderWizard(
      makeWizard({ phase: 'review', source: makeReviewSource(), confirming: true }),
    );
    const confirmBtn = screen.getByRole('button', { name: /confirming/i });
    expect(confirmBtn).toBeDisabled();
  });

  // ── Error ───────────────────────────────────────────────────────────────

  it('shows the error dialog during phase "error"', () => {
    renderWizard(makeWizard({ phase: 'error', error: 'Upload failed: boom' }));
    expect(screen.getByText(/Something went wrong/i)).toBeInTheDocument();
    expect(screen.getByText(/Upload failed: boom/i)).toBeInTheDocument();
  });
});
