// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { makeWrapper } from '../../../test/renderWithProviders';
import { ExtractionOptions } from '../ExtractionOptions';

type Props = Parameters<typeof ExtractionOptions>[0];

function makeProps(overrides: Partial<Props> = {}): Props {
  return {
    extractEntities: true,
    onExtractEntitiesChange: vi.fn(),
    enableVision: true,
    onEnableVisionChange: vi.fn(),
    showNormalizationOption: true,
    enableNormalization: true,
    onNormalizationChange: vi.fn(),
    analysisDepth: 'full',
    onAnalysisDepthChange: vi.fn(),
    contentFiltering: true,
    onContentFilteringChange: vi.fn(),
    filteringMode: '',
    onFilteringModeChange: vi.fn(),
    skipDuplicates: false,
    onSkipDuplicatesChange: vi.fn(),
    ...overrides,
  };
}

function renderExpanded(overrides: Partial<Props> = {}) {
  render(<ExtractionOptions {...makeProps(overrides)} />, { wrapper: makeWrapper() });
  // Advanced is collapsed by default — open it.
  fireEvent.click(screen.getByRole('button', { name: /advanced/i }));
}

describe('<ExtractionOptions />', () => {
  beforeEach(() => vi.clearAllMocks());

  // Asserts each element precedes the next in document order.
  function expectOrder(...els: HTMLElement[]) {
    for (let i = 0; i < els.length - 1; i++) {
      expect(els[i].compareDocumentPosition(els[i + 1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  }

  it('groups the toggles under EXTRACTION then PROCESSING labels', () => {
    renderExpanded();
    const extraction = screen.getByText('EXTRACTION');
    const processing = screen.getByText('PROCESSING');
    // EXTRACTION comes before PROCESSING in the layout.
    expectOrder(extraction, processing);
  });

  it('orders EXTRACTION as filtering mode → content filtering → extract entities → quick analysis', () => {
    renderExpanded();
    expectOrder(
      screen.getByRole('combobox', { name: /filtering mode/i }),
      screen.getByText('Content filtering'),
      screen.getByText('Extract entities'),
      screen.getByText('Quick analysis'),
    );
  });

  it('puts Vision, Text normalization, and Skip duplicate files under PROCESSING', () => {
    renderExpanded();
    const processing = screen.getByText('PROCESSING');
    const vision = screen.getByText('Vision processing');
    const normalization = screen.getByText('Text normalization');
    const skip = screen.getByText(/skip duplicate files/i);
    // All three follow the PROCESSING label.
    expectOrder(processing, vision, normalization, skip);
  });

  it('collapses the EXTRACTION knobs to just the master toggle when entities are off', () => {
    renderExpanded({ extractEntities: false });
    expect(screen.getByText('Extract entities')).toBeInTheDocument();
    expect(screen.queryByText('Content filtering')).not.toBeInTheDocument();
    expect(screen.queryByText('Quick analysis')).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: /filtering mode/i })).not.toBeInTheDocument();
  });

  it('shows the Filtering Mode dropdown inside EXTRACTION when entities are on', () => {
    renderExpanded();
    expect(screen.getByRole('combobox', { name: /filtering mode/i })).toBeInTheDocument();
  });

  it('keeps Content filtering help in a tooltip, not inline', () => {
    renderExpanded();
    expect(screen.getByText('Content filtering')).toBeInTheDocument();
    expect(screen.queryByText(/Filters non-essential content/i)).not.toBeInTheDocument();
  });

  it('no longer renders the domain dropdown (promoted to the parent)', () => {
    renderExpanded();
    expect(screen.queryByRole('combobox', { name: /^domain$/i })).not.toBeInTheDocument();
  });
});
