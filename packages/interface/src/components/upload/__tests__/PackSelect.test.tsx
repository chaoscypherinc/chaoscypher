// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from 'react';
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

  it('at upload, auto leaves the choice to detection and renders no picker', () => {
    renderSelect({ value: null, allowAuto: true });
    expect(screen.getByRole('switch', { name: /detect packs automatically/i })).toBeChecked();
    expect(screen.queryByRole('combobox', { name: /packs/i })).not.toBeInTheDocument();
    expect(screen.getByTestId('pack-select-helper')).toHaveTextContent(
      'Detection proposes up to two packs; you review them before extraction starts.',
    );
  });

  it('turning auto off chooses the floor alone and shows the picker', () => {
    const onChange = vi.fn();
    /** A parent that holds the value, as the upload dialog does. */
    function Harness() {
      const [packs, setPacks] = useState<string[] | null>(null);
      return (
        <PackSelect
          value={packs}
          availableDomains={DOMAINS}
          onChange={(next) => {
            onChange(next);
            setPacks(next);
          }}
          allowAuto
        />
      );
    }
    render(<Harness />, { wrapper: makeWrapper() });
    expect(screen.queryByRole('combobox', { name: /packs/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('switch', { name: /detect packs automatically/i }));
    expect(onChange).toHaveBeenCalledWith([]);
    const picker = screen.getByRole('combobox', { name: /packs/i });
    expect(picker).toBeEnabled();
    expect(screen.getByTestId('pack-select-helper')).toHaveTextContent(/floor only/i);

    // and back on: the picker goes away again
    fireEvent.click(screen.getByRole('switch', { name: /detect packs automatically/i }));
    expect(onChange).toHaveBeenLastCalledWith(null);
    expect(screen.queryByRole('combobox', { name: /packs/i })).not.toBeInTheDocument();
  });

  it('turning auto back on emits null', () => {
    const onChange = renderSelect({ value: ['literary'], allowAuto: true });
    fireEvent.click(screen.getByRole('switch', { name: /detect packs automatically/i }));
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it('explains packs on hover, naming built-in packs from the registered list', async () => {
    renderSelect({ value: null, allowAuto: true });
    fireEvent.mouseOver(screen.getByLabelText('What are packs?'));
    const tooltip = await screen.findByRole('tooltip');
    expect(tooltip).toHaveTextContent(
      'Packs teach the extractor the vocabulary of a subject. Each pack adds the kinds of things ' +
        'and relationships to look for in that field, for example Historical and Literary. With ' +
        'detection on, ChaosCypher reads a sample of your document and picks the packs that fit, ' +
        'and you review them before extraction starts.',
    );
    // the examples come from the registered packs: built-in ones only, never the floor
    expect(tooltip).not.toHaveTextContent(/genomics/i);
    expect(tooltip).not.toHaveTextContent(/generic/i);
  });

  it('names at most three example packs, whichever the registry lists', async () => {
    const shipped: ExtractionDomain[] = [
      { name: 'generic', description: 'The floor', builtin: true, role: 'floor' },
      { name: 'legal', description: 'Law', builtin: true, role: 'pack' },
      { name: 'medical', description: 'Medicine', builtin: true, role: 'pack' },
      { name: 'scientific', description: 'Science', builtin: true, role: 'pack' },
      { name: 'technical', description: 'Software', builtin: true, role: 'pack' },
    ];
    renderSelect({ value: null, allowAuto: true, availableDomains: shipped });
    fireEvent.mouseOver(screen.getByLabelText('What are packs?'));
    const tooltip = await screen.findByRole('tooltip');
    expect(tooltip).toHaveTextContent('for example Legal, Medical and Scientific.');
    expect(tooltip).not.toHaveTextContent(/technical/i);
  });

  it('says a whole-site import extracts with the detected packs without a review', async () => {
    renderSelect({ value: null, allowAuto: true, proposalReviewed: false });
    expect(screen.getByTestId('pack-select-helper')).toHaveTextContent(
      'Detection picks up to two packs for each page; the pages extract with them straight away.',
    );
    fireEvent.mouseOver(screen.getByLabelText('What are packs?'));
    const tooltip = await screen.findByRole('tooltip');
    expect(tooltip).toHaveTextContent(
      'With detection on, ChaosCypher reads a sample of each page and picks the packs that fit; ' +
        'a whole-site import extracts with them straight away, without a review.',
    );
    expect(tooltip).not.toHaveTextContent(/you review them/i);
  });

  it('without allowAuto (the confirm step, source settings) shows the picker, no switch and no explainer', () => {
    renderSelect({ value: [] });
    expect(screen.getByRole('combobox', { name: /packs/i })).toBeEnabled();
    expect(screen.queryByRole('switch')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('What are packs?')).not.toBeInTheDocument();
  });
});
