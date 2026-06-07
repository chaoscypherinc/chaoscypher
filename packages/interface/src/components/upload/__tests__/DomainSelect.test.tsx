// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { makeWrapper } from '../../../test/renderWithProviders';
import { DomainSelect } from '../DomainSelect';
import type { ExtractionDomain } from '../../../services/api/sourceProcessing';

const DOMAINS: ExtractionDomain[] = [
  {
    name: 'medicine',
    description: 'Biomedical and clinical text: diseases, drugs, anatomy.',
    builtin: true,
    icon: 'MenuBook',
    prompt_tokens: 0,
  },
  {
    name: 'genomics',
    description: 'Genes, variants, and sequencing pipelines.',
    builtin: false,
    prompt_tokens: 15000,
  },
];

// Capacity chosen so `medicine` (0 prompt tokens) sits ~18% of the window while
// `genomics` (15k prompt tokens) blows past 90% → only genomics gets the marker.
const CAPACITY = { contextWindow: 20000, groupSize: 1, inputPerChunk: 100, outputPerChunk: 1000 };

function renderSelect(overrides: Record<string, unknown> = {}) {
  const onDomainChange = vi.fn();
  render(
    <DomainSelect
      selectedDomain="__auto__"
      availableDomains={DOMAINS}
      onDomainChange={onDomainChange}
      {...CAPACITY}
      {...overrides}
    />,
    { wrapper: makeWrapper() },
  );
  return { onDomainChange };
}

describe('<DomainSelect />', () => {
  beforeEach(() => vi.clearAllMocks());

  it('offers Auto + every domain and reports the picked domain', () => {
    const { onDomainChange } = renderSelect();
    fireEvent.mouseDown(screen.getByRole('combobox', { name: /domain/i }));

    expect(screen.getByRole('option', { name: /auto \(recommended\)/i })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /medicine/i })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /genomics/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('option', { name: /medicine/i }));
    expect(onDomainChange).toHaveBeenCalledWith('medicine');
  });

  it('keeps rows lean — name shows, description does not render inline', () => {
    renderSelect();
    fireEvent.mouseDown(screen.getByRole('combobox', { name: /domain/i }));

    expect(screen.getByRole('option', { name: /medicine/i })).toBeInTheDocument();
    // The description belongs in the tooltip, not as inline row text.
    expect(screen.queryByText(/Biomedical and clinical text/i)).not.toBeInTheDocument();
  });

  it('flags only domains that would overflow the context window', () => {
    renderSelect();
    fireEvent.mouseDown(screen.getByRole('combobox', { name: /domain/i }));

    const warnings = screen.getAllByTitle(/exceeds the context window/i);
    expect(warnings).toHaveLength(1);
    const genomics = screen.getByRole('option', { name: /genomics/i });
    expect(within(genomics).getByTitle(/exceeds the context window/i)).toBeInTheDocument();
  });

  it('exposes the description + token cost in the row tooltip on hover', async () => {
    renderSelect();
    fireEvent.mouseDown(screen.getByRole('combobox', { name: /domain/i }));
    // Hover the row content (the Tooltip's child), not the <li> option wrapper.
    fireEvent.mouseOver(screen.getByText('Medicine'));

    const tip = await screen.findByRole('tooltip');
    expect(tip).toHaveTextContent(/Biomedical and clinical text/i);
    expect(tip).toHaveTextContent(/tokens/i);
  });
});
