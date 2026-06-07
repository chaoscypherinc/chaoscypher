// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { SectionHeader } from '../SectionHeader';

describe('SectionHeader', () => {
  it('renders the label uppercase via styling and shows the text', () => {
    render(<SectionHeader label="Extraction performance" />);
    expect(screen.getByText('Extraction performance')).toBeInTheDocument();
  });

  it('renders an icon when provided', () => {
    render(<SectionHeader label="Vision pages" icon={<span data-testid="ico" />} />);
    expect(screen.getByTestId('ico')).toBeInTheDocument();
  });
});
