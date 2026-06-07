// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { makeWrapper } from '../../../test/renderWithProviders';
import { FilteringModeSelect } from '../FilteringModeSelect';

function renderSelect(filteringMode = '') {
  const onFilteringModeChange = vi.fn();
  render(
    <FilteringModeSelect filteringMode={filteringMode} onFilteringModeChange={onFilteringModeChange} />,
    { wrapper: makeWrapper() },
  );
  return { onFilteringModeChange };
}

describe('<FilteringModeSelect />', () => {
  beforeEach(() => vi.clearAllMocks());

  it('offers the Auto default plus every filtering mode', () => {
    renderSelect();
    fireEvent.mouseDown(screen.getByRole('combobox', { name: /filtering mode/i }));

    expect(screen.getByRole('option', { name: /auto \(recommended\)/i })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /maximum \(5\)/i })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /balanced \(3\)/i })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /unfiltered \(0\)/i })).toBeInTheDocument();
  });

  it('reports the chosen mode value', () => {
    const { onFilteringModeChange } = renderSelect();
    fireEvent.mouseDown(screen.getByRole('combobox', { name: /filtering mode/i }));
    fireEvent.click(screen.getByRole('option', { name: /balanced \(3\)/i }));
    expect(onFilteringModeChange).toHaveBeenCalledWith('balanced');
  });

  it('maps the Auto default back to an empty string', () => {
    const { onFilteringModeChange } = renderSelect('balanced');
    fireEvent.mouseDown(screen.getByRole('combobox', { name: /filtering mode/i }));
    fireEvent.click(screen.getByRole('option', { name: /auto \(recommended\)/i }));
    expect(onFilteringModeChange).toHaveBeenCalledWith('');
  });

  it('keeps rows lean — the per-mode description is not inline', () => {
    renderSelect();
    fireEvent.mouseDown(screen.getByRole('combobox', { name: /filtering mode/i }));
    // "Balanced (3)" label shows, but its secondary description lives in the tooltip.
    expect(screen.getByRole('option', { name: /balanced \(3\)/i })).toBeInTheDocument();
    expect(screen.queryByText(/Best for general-purpose documents/i)).not.toBeInTheDocument();
  });
});
