// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import MetadataCard from '../MetadataCard';
import MetadataRow from '../MetadataRow';

describe('MetadataCard', () => {
  it('renders children directly when not collapsible', () => {
    render(
      <MetadataCard>
        <MetadataRow label="ID">abc-123</MetadataRow>
      </MetadataCard>,
    );
    expect(screen.getByText('abc-123')).toBeTruthy();
    // No toggle button in the non-collapsible variant.
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('shows only the summary when collapsed, then reveals children on expand', async () => {
    render(
      <MetadataCard collapsible summary={<span>summary text</span>}>
        <MetadataRow label="ID">hidden-detail</MetadataRow>
      </MetadataCard>,
    );

    // Collapsed by default: summary visible, children unmounted.
    expect(screen.getByText('summary text')).toBeTruthy();
    expect(screen.queryByText('hidden-detail')).toBeNull();

    // Expand: children appear, summary goes away.
    fireEvent.click(screen.getByRole('button', { name: /expand metadata/i }));
    expect(screen.getByText('hidden-detail')).toBeTruthy();
    expect(screen.queryByText('summary text')).toBeNull();

    // Collapse again — the summary returns immediately; children unmount once
    // the collapse transition completes (unmountOnExit), so await that.
    fireEvent.click(screen.getByRole('button', { name: /collapse metadata/i }));
    expect(screen.getByText('summary text')).toBeTruthy();
    await waitFor(() => expect(screen.queryByText('hidden-detail')).toBeNull());
  });

  it('starts expanded when defaultExpanded is set', () => {
    render(
      <MetadataCard collapsible defaultExpanded summary={<span>summary text</span>}>
        <MetadataRow label="ID">visible-detail</MetadataRow>
      </MetadataCard>,
    );
    expect(screen.getByText('visible-detail')).toBeTruthy();
    expect(screen.queryByText('summary text')).toBeNull();
  });
});
