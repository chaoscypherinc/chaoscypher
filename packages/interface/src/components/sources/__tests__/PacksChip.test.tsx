// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PacksChip } from '../PacksChip';
import { packsLabel, sourcePacks } from '../../../utils/packs';

describe('PacksChip', () => {
  it('shows the + label with one icon per pack, in the stored order', () => {
    render(
      <PacksChip
        source={{
          extraction_domain: 'historical+literary',
          extraction_packs: ['literary', 'historical'],
          extraction_pack_icons: { literary: 'MenuBook', historical: 'HistoryEdu' },
        }}
        color="#fff"
      />,
    );
    expect(screen.getByText('Literary + Historical')).toBeInTheDocument();
    expect(screen.getByTestId('pack-icon-literary')).toBeInTheDocument();
    expect(screen.getByTestId('pack-icon-historical')).toBeInTheDocument();
  });

  it('reads an old label when the row has no list, and the floor as no pack', () => {
    expect(sourcePacks({ extraction_domain: 'historical+literary' })).toEqual(['historical', 'literary']);
    expect(sourcePacks({ extraction_domain: 'generic' })).toEqual([]);
    expect(sourcePacks({ extraction_packs: [] })).toEqual([]);
    expect(sourcePacks({})).toBeNull();
    expect(packsLabel([])).toBe('Floor only');
  });

  it('renders "Floor only" for a floor-only source and nothing for an unextracted one', () => {
    const { container, rerender } = render(
      <PacksChip source={{ extraction_domain: 'generic', extraction_packs: [] }} color="#fff" />,
    );
    expect(screen.getByText('Floor only')).toBeInTheDocument();
    rerender(<PacksChip source={{}} color="#fff" />);
    expect(container).toBeEmptyDOMElement();
  });
});
