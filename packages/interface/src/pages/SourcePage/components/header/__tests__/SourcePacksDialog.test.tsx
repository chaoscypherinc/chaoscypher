// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { makeWrapper } from '../../../../../test/renderWithProviders';
import { SourcePacksDialog } from '../SourcePacksDialog';
import type { ExtractionDomain } from '../../../../../services/api/sourceProcessing';
import type { Source } from '../../../../../types';

const DOMAINS: ExtractionDomain[] = [
  { name: 'generic', description: 'The floor', builtin: true, role: 'floor' },
  { name: 'historical', description: 'History', builtin: true, role: 'pack' },
  { name: 'literary', description: 'Fiction', builtin: true, role: 'pack' },
];

function source(overrides: Partial<Source> = {}): Source {
  return {
    id: 's1',
    filename: 'book.txt',
    status: 'committed',
    extraction_domain: 'literary',
    extraction_packs: ['literary'],
    ...overrides,
  } as Source;
}

function renderDialog(s: Source, onConfirm = vi.fn()) {
  render(
    <SourcePacksDialog
      open
      source={s}
      availableDomains={DOMAINS}
      submitting={false}
      onClose={vi.fn()}
      onConfirm={onConfirm}
    />,
    { wrapper: makeWrapper() },
  );
  return onConfirm;
}

describe('SourcePacksDialog', () => {
  it('starts from the packs the source was extracted with and warns of the re-extraction', () => {
    renderDialog(source());
    const area = screen.getByRole('combobox', { name: /packs/i }).closest('.MuiAutocomplete-root') as HTMLElement;
    expect(within(area).getByRole('button', { name: /literary/i })).toBeInTheDocument();
    expect(screen.getByText(/re-extracts this source/i)).toHaveTextContent(/graph data .* is deleted first/i);
    // unchanged: nothing to re-extract
    expect(screen.getByRole('button', { name: /re-extract with these packs/i })).toBeDisabled();
  });

  it('confirms a changed choice (here: the floor alone)', () => {
    const onConfirm = renderDialog(source());
    const area = screen.getByRole('combobox', { name: /packs/i }).closest('.MuiAutocomplete-root') as HTMLElement;
    fireEvent.click(within(within(area).getByRole('button', { name: /literary/i })).getByTestId('CancelIcon'));
    fireEvent.click(screen.getByRole('button', { name: /re-extract with these packs/i }));
    expect(onConfirm).toHaveBeenCalledWith([]);
  });

  it('an indexed source is warned the model runs, without a graph to delete', () => {
    renderDialog(source({ status: 'indexed', extraction_packs: null, extraction_domain: null } as Partial<Source>));
    expect(screen.getByText(/re-extracts this source/i)).not.toHaveTextContent(/deleted first/i);
  });

  it('keeps the picker, with no detect switch or explainer', () => {
    renderDialog(source());
    expect(screen.getByRole('combobox', { name: /packs/i })).toBeEnabled();
    expect(screen.queryByRole('switch', { name: /detect packs automatically/i })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('What are packs?')).not.toBeInTheDocument();
  });
});
