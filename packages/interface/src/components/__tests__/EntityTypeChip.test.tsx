// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { EntityTypeChip } from '../EntityTypeChip';
import { entitySubtype, subtypeIcons, typeLabel } from '../../utils/entityTypes';

describe('EntityTypeChip', () => {
  it('labels the core type with the pack subtype', () => {
    render(<EntityTypeChip coreType="Person" subtype="military_unit" color="#7d41d6" />);
    expect(screen.getByText('Person · Military Unit')).toBeInTheDocument();
  });

  it('shows the core type alone without a subtype, in the core type colour', () => {
    render(<EntityTypeChip coreType="Location" color="#d641b0" />);
    const chip = screen.getByText('Location').closest('.MuiChip-root') as HTMLElement;
    expect(chip).toHaveStyle({ color: '#d641b0' });
  });

  it('reads the subtype off the properties and the icons off the packs', () => {
    expect(entitySubtype({ entity_subtype: 'character' })).toBe('character');
    expect(entitySubtype({ entity_subtype: '' })).toBeUndefined();
    expect(entitySubtype(undefined)).toBeUndefined();
    expect(typeLabel('Person', 'character')).toBe('Person · Character');
    const icons = subtypeIcons([
      {
        name: 'literary', description: '', builtin: true, role: 'pack',
        subtypes: [
          { key: 'character', name: 'Character', of: 'Person', icon: 'Face' },
          { key: 'theme', name: 'Theme', of: 'Concept', icon: null },
        ],
      },
      {
        name: 'historical', description: '', builtin: true, role: 'pack',
        subtypes: [{ key: 'character', name: 'Character', of: 'Person', icon: 'Person' }],
      },
    ]);
    expect([...icons]).toEqual([['character', 'Face']]);
  });
});
