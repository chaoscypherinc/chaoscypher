// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { makeWrapper } from '../../../../test/renderWithProviders';
import { ConfirmExtractionDialog } from '../ConfirmExtractionDialog';
import { proposedPacks } from '../../../../utils/packs';
import type { UnifiedSource } from '../../../../types';
import type { ExtractionDomain } from '../../../../services/api/sourceProcessing';

const DOMAINS: ExtractionDomain[] = [
  { name: 'historical', description: 'History', builtin: true, role: 'pack', icon: 'HistoryEdu' },
  { name: 'literary', description: 'Fiction', builtin: true, role: 'pack', icon: 'MenuBook' },
  { name: 'legal', description: 'Legal', builtin: true, role: 'pack' },
  { name: 'medical', description: 'Medical', builtin: true, role: 'pack' },
  { name: 'generic', description: 'The floor', builtin: true, role: 'floor' },
];

function makeSource(overrides: Partial<UnifiedSource> = {}): UnifiedSource {
  return {
    id: 'src-1',
    stage: 'queued',
    title: 'war-and-peace.txt',
    source_type: 'text',
    size: 2048,
    status: 'awaiting_confirmation',
    created_at: '2026-10-08T00:00:00Z',
    confirmation_required: true,
    detection_confidence: 0.82,
    // the ranking still lists every candidate; the dialog shows the proposal, not the top 3
    detection_ranking: [
      { domain: 'literary', score: 0.9 },
      { domain: 'historical', score: 0.85 },
      { domain: 'legal', score: 0.4 },
      { domain: 'medical', score: 0.3 },
    ],
    // The real proposal blob (confirmation_gate.proposal_from_detection):
    // ranking, confidence, packs, detected_domain, reason — never options.
    proposed_extraction_options: {
      packs: ['literary', 'historical'],
      detected_domain: 'literary',
      reason: 'packs_within_margin',
    },
    ...overrides,
  };
}

function renderDialog(source: UnifiedSource, onConfirm = vi.fn()) {
  render(
    <ConfirmExtractionDialog
      open
      source={source}
      availableDomains={DOMAINS}
      submitting={false}
      onClose={vi.fn()}
      onConfirm={onConfirm}
    />,
    { wrapper: makeWrapper() },
  );
  return onConfirm;
}

describe('<ConfirmExtractionDialog />', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows the proposed packs as selected chips, not the top-3 domains', () => {
    renderDialog(makeSource());
    expect(screen.getByTestId('proposed-packs')).toHaveTextContent(
      'Detection proposes Literary + Historical (two packs scored close together)',
    );
    const packs = screen.getByRole('combobox', { name: /packs/i }).closest('.MuiAutocomplete-root') as HTMLElement;
    expect(within(packs).getByRole('button', { name: /literary/i })).toBeInTheDocument();
    expect(within(packs).getByRole('button', { name: /historical/i })).toBeInTheDocument();
    // no ranking quick-picks or score chips any more
    expect(screen.queryByText('0.9')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /legal/i })).not.toBeInTheDocument();
  });

  it('sends the proposed packs and no option the user did not change', () => {
    // The confirm request keeps the upload-time choice for every field it
    // does not carry; seeding from the proposal used to send
    // content_filtering: true over an upload that had turned it off.
    const onConfirm = renderDialog(makeSource());
    fireEvent.click(screen.getByRole('button', { name: /^confirm$/i }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    const payload = onConfirm.mock.calls[0][0];
    expect(payload.packs).toEqual(['literary', 'historical']);
    expect(payload.domain).toBeUndefined();
    expect(payload.analysis_depth).toBeUndefined();
    expect(payload.filtering_mode).toBeUndefined();
    expect(payload.content_filtering).toBeUndefined();
  });

  it('sends an option once the user changes it', () => {
    const onConfirm = renderDialog(makeSource());
    fireEvent.click(screen.getByText(/advanced/i));
    fireEvent.click(screen.getByLabelText(/quick analysis/i));
    fireEvent.click(screen.getByLabelText(/content filtering/i));
    fireEvent.click(screen.getByRole('button', { name: /^confirm$/i }));
    const payload = onConfirm.mock.calls[0][0];
    expect(payload.analysis_depth).toBe('quick');
    expect(payload.content_filtering).toBe(false);
  });

  it('offers only the options the confirm endpoint accepts', () => {
    // "Extract entities", "Vision processing" and "Skip duplicate files" are
    // upload-time choices the confirm request has no fields for.
    renderDialog(makeSource());
    fireEvent.click(screen.getByText(/advanced/i));
    expect(screen.queryByLabelText(/extract entities/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/vision processing/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/skip duplicate/i)).not.toBeInTheDocument();
    expect(screen.getByLabelText(/quick analysis/i)).toBeInTheDocument();
  });

  it('sends no packs while the registered packs have not loaded, then reseeds when they arrive', () => {
    const onConfirm = vi.fn();
    const source = makeSource();
    const { rerender } = render(
      <ConfirmExtractionDialog
        open
        source={source}
        availableDomains={[]}
        submitting={false}
        onClose={vi.fn()}
        onConfirm={onConfirm}
      />,
      { wrapper: makeWrapper() },
    );
    expect(screen.getByTestId('packs-loading')).toBeInTheDocument();
    expect(screen.queryByText(/found no pack above the floor/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^confirm$/i }));
    expect(onConfirm.mock.calls[0][0].packs).toBeUndefined();

    rerender(
      <ConfirmExtractionDialog
        open
        source={source}
        availableDomains={DOMAINS}
        submitting={false}
        onClose={vi.fn()}
        onConfirm={onConfirm}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /^confirm$/i }));
    expect(onConfirm.mock.calls[1][0].packs).toEqual(['literary', 'historical']);
  });

  it('removing a chip sends the remaining packs; removing all sends the floor alone', () => {
    const onConfirm = renderDialog(makeSource());
    const packs = screen.getByRole('combobox', { name: /packs/i }).closest('.MuiAutocomplete-root') as HTMLElement;
    const historical = within(packs).getByRole('button', { name: /historical/i });
    fireEvent.click(within(historical).getByTestId('CancelIcon'));
    fireEvent.click(screen.getByRole('button', { name: /^confirm$/i }));
    expect(onConfirm.mock.calls[0][0].packs).toEqual(['literary']);

    const literary = within(packs).getByRole('button', { name: /literary/i });
    fireEvent.click(within(literary).getByTestId('CancelIcon'));
    fireEvent.click(screen.getByRole('button', { name: /^confirm$/i }));
    expect(onConfirm.mock.calls[1][0].packs).toEqual([]);
  });

  it('selects the floor alone when detection proposed no pack', () => {
    const onConfirm = renderDialog(
      makeSource({
        detection_low_confidence: true,
        detection_ranking: [],
        proposed_extraction_options: { packs: [], reason: 'no_pack_above_floor' },
      }),
    );
    expect(screen.getByText(/found no pack above the floor/i)).toBeInTheDocument();
    expect(screen.getByTestId('pack-select-helper')).toHaveTextContent(/floor only/i);
    fireEvent.click(screen.getByRole('button', { name: /^confirm$/i }));
    expect(onConfirm.mock.calls[0][0].packs).toEqual([]);
  });

  it('shows the no-text prompt for an image-only proposal', () => {
    renderDialog(makeSource({ proposed_extraction_options: { packs: [], no_text: true } }));
    expect(screen.getByText(/not enough text to detect/i)).toBeInTheDocument();
  });

  it('reads an older one-domain proposal as one pack, and never the floor as a pack', () => {
    expect(proposedPacks(makeSource({ proposed_extraction_options: { domain: 'legal' } }), DOMAINS)).toEqual([
      'legal',
    ]);
    expect(proposedPacks(makeSource({ proposed_extraction_options: { domain: 'generic' } }), DOMAINS)).toEqual(
      [],
    );
    expect(proposedPacks(makeSource({ proposed_extraction_options: { packs: ['gone'] } }), DOMAINS)).toEqual(
      [],
    );
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

  it('keeps the picker with the proposal chips, and no detect switch or explainer', () => {
    renderDialog(makeSource());
    expect(screen.getByRole('combobox', { name: /packs/i })).toBeEnabled();
    expect(screen.queryByRole('switch', { name: /detect packs automatically/i })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('What are packs?')).not.toBeInTheDocument();
  });
});
