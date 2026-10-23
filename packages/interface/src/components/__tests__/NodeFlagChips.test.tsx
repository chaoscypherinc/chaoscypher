// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { NodeFlagChips } from '../NodeFlagChips';

describe('NodeFlagChips', () => {
  it('renders one chip per flag, in table order', () => {
    render(<NodeFlagChips properties={{ descriptor_reference: true, orphan: true }} />);
    const chips = screen.getAllByText(/^(Unconnected|Reference)$/);
    expect(chips.map((chip) => chip.textContent)).toEqual(['Unconnected', 'Reference']);
  });

  it('explains each flag in an accessible tooltip label', () => {
    render(<NodeFlagChips properties={{ orphan: true }} />);
    expect(screen.getByLabelText(/Had no relationship when it was committed/)).toBeInTheDocument();
  });

  it('renders nothing for an unflagged node or a non-boolean flag value', () => {
    const { container, rerender } = render(<NodeFlagChips properties={{ name: 'x' }} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<NodeFlagChips properties={{ orphan: 'true' }} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<NodeFlagChips properties={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });
});
