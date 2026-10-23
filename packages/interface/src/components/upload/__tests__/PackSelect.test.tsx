// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { makeWrapper } from '../../../test/renderWithProviders';
import { PackSelect } from '../PackSelect';
import { packsOf } from '../../../utils/packs';
import type { ExtractionDomain } from '../../../services/api/sourceProcessing';

const DOMAINS: ExtractionDomain[] = [
  { name: 'generic', description: 'The floor', builtin: true, role: 'floor', prompt_tokens: 1000 },
  { name: 'historical', description: 'History', builtin: true, role: 'pack', icon: 'HistoryEdu', prompt_tokens: 300 },
  { name: 'literary', description: 'Fiction', builtin: true, role: 'pack', icon: 'MenuBook', prompt_tokens: 400 },
  { name: 'genomics', description: 'Genes', builtin: false, role: 'pack', prompt_tokens: 15000 },
];

function renderSelect(props: Partial<Parameters<typeof PackSelect>[0]> = {}) {
  const onChange = vi.fn();
  render(
    <PackSelect
      value={['literary', 'historical']}
      availableDomains={DOMAINS}
      onChange={onChange}
      proposed={['literary', 'historical']}
      contextWindow={20000}
      groupSize={1}
      inputPerChunk={100}
      outputPerChunk={1000}
      {...props}
    />,
    { wrapper: makeWrapper() },
  );
  return onChange;
}

function chipsArea(): HTMLElement {
  return screen.getByRole('combobox', { name: /packs/i }).closest('.MuiAutocomplete-root') as HTMLElement;
}

describe('<PackSelect />', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders the proposal as selected chips, filled as proposed', () => {
    renderSelect();
    const literary = within(chipsArea()).getByRole('button', { name: /literary/i });
    const historical = within(chipsArea()).getByRole('button', { name: /historical/i });
    expect(literary).toHaveClass('MuiChip-filled');
    expect(historical).toHaveAttribute('title', 'Proposed by detection');
    expect(screen.getByTestId('pack-select-helper')).toHaveTextContent('Floor + Literary + Historical');
  });

  it('emits packs: [] when cleared (the floor alone)', () => {
    const onChange = renderSelect();
    // the clear button shows once the input has focus
    fireEvent.focus(screen.getByRole('combobox', { name: /packs/i }));
    fireEvent.click(within(chipsArea()).getByTitle('Clear'));
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it('shows "Floor only" with no chip', () => {
    renderSelect({ value: [] });
    expect(within(chipsArea()).queryByRole('button', { name: /literary/i })).not.toBeInTheDocument();
    expect(screen.getByTestId('pack-select-helper')).toHaveTextContent(/floor only/i);
  });

  it('offers every pack but never the floor, and adds a picked one', () => {
    const onChange = renderSelect({ value: ['literary'] });
    fireEvent.mouseDown(screen.getByRole('combobox', { name: /packs/i }));
    const listbox = screen.getByRole('listbox');
    expect(within(listbox).getByRole('option', { name: /historical/i })).toBeInTheDocument();
    expect(within(listbox).getByRole('option', { name: /genomics/i })).toBeInTheDocument();
    expect(within(listbox).queryByRole('option', { name: /generic/i })).not.toBeInTheDocument();
    // a selected pack is not offered twice
    expect(within(listbox).queryByRole('option', { name: /literary/i })).not.toBeInTheDocument();
    fireEvent.click(within(listbox).getByRole('option', { name: /historical/i }));
    expect(onChange).toHaveBeenCalledWith(['literary', 'historical']);
    expect(packsOf(DOMAINS).map((d) => d.name)).toEqual(['historical', 'literary', 'genomics']);
  });

  it('warns when the selection would overflow the context window', () => {
    renderSelect({ value: ['genomics'] });
    expect(screen.getByTitle('Exceeds the context window')).toBeInTheDocument();
  });

  it('at upload, auto leaves the choice to detection; switching it off chooses the floor alone', () => {
    const onChange = renderSelect({ value: null, allowAuto: true });
    const auto = screen.getByRole('switch', { name: /detect packs automatically/i });
    expect(auto).toBeChecked();
    expect(screen.getByRole('combobox', { name: /packs/i })).toBeDisabled();
    expect(screen.getByTestId('pack-select-helper')).toHaveTextContent(/detection proposes up to two packs/i);
    fireEvent.click(auto);
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it('turning auto back on emits null', () => {
    const onChange = renderSelect({ value: ['literary'], allowAuto: true });
    fireEvent.click(screen.getByRole('switch', { name: /detect packs automatically/i }));
    expect(onChange).toHaveBeenCalledWith(null);
  });
});
