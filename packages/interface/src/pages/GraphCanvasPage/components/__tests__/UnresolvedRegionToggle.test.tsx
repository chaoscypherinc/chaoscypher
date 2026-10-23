// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { UnresolvedRegionToggle } from '../UnresolvedRegionToggle';

describe('UnresolvedRegionToggle', () => {
  it('renders nothing when no node is flagged', () => {
    const { container } = render(
      <UnresolvedRegionToggle count={0} expanded onToggle={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the count and the expanded state, and toggles on click', () => {
    const onToggle = vi.fn<() => void>();
    const { rerender } = render(
      <UnresolvedRegionToggle count={4} expanded onToggle={onToggle} />,
    );
    const button = screen.getByRole('button', { name: /Collapse the Unresolved region/ });
    expect(button).toHaveTextContent('Unresolved (4)');
    expect(button).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(button);
    expect(onToggle).toHaveBeenCalledTimes(1);

    rerender(<UnresolvedRegionToggle count={4} expanded={false} onToggle={onToggle} />);
    expect(screen.getByRole('button', { name: /Expand the Unresolved region/ })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
  });
});
