// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { makeWrapper } from '../../test/renderWithProviders';
import { UploadDialog } from '../UploadDialog';
import type { ExtractionDomain } from '../../services/api/sources';

const DOMAINS: ExtractionDomain[] = [
  { name: 'scientific', description: 'Science', builtin: true, role: 'pack' },
  { name: 'legal', description: 'Legal', builtin: true, role: 'pack' },
];

type Props = Parameters<typeof UploadDialog>[0];

function makeProps(overrides: Partial<Props> = {}): Props {
  return {
    open: true,
    onClose: vi.fn(),
    selectedFiles: [],
    analysisDepth: 'full',
    enableNormalization: true,
    selectedPacks: null,
    availableDomains: DOMAINS,
    onFilesSelected: vi.fn(),
    onAnalysisDepthChange: vi.fn(),
    onNormalizationChange: vi.fn(),
    onPacksChange: vi.fn(),
    onConfirm: vi.fn(),
    onClearSelection: vi.fn(),
    onRemoveFile: vi.fn(),
    onUrlImport: vi.fn().mockResolvedValue(undefined),
    onSiteImport: vi.fn().mockResolvedValue(undefined),
    importingUrl: false,
    extractEntities: true,
    onExtractEntitiesChange: vi.fn(),
    enableVision: true,
    onEnableVisionChange: vi.fn(),
    filteringMode: '',
    onFilteringModeChange: vi.fn(),
    contentFiltering: true,
    onContentFilteringChange: vi.fn(),
    contextWindow: 8192,
    groupSize: 4,
    inputPerChunk: 150,
    outputPerChunk: 2000,
    skipDuplicates: false,
    onSkipDuplicatesChange: vi.fn(),
    ...overrides,
  };
}

function file(name = 'doc.pdf') {
  return new File(['hello'], name, { type: 'application/pdf' });
}

describe('<UploadDialog /> pack selector placement', () => {
  // PackSelect is loaded lazily (the entry chunk's size budget): resolve it once,
  // so every test below renders it synchronously and a "hidden" assertion
  // cannot pass merely because the chunk had not arrived yet.
  beforeAll(async () => {
    const { unmount } = render(<UploadDialog {...makeProps({ selectedFiles: [file()] })} />, {
      wrapper: makeWrapper(),
    });
    await screen.findByRole('switch', { name: /detect packs automatically/i });
    unmount();
  });
  beforeEach(() => vi.clearAllMocks());

  it('surfaces the pack choice once a file is selected: detection on, no picker', () => {
    render(<UploadDialog {...makeProps({ selectedFiles: [file()] })} />, { wrapper: makeWrapper() });
    expect(screen.getByRole('switch', { name: /detect packs automatically/i })).toBeChecked();
    expect(screen.queryByRole('combobox', { name: /packs/i })).not.toBeInTheDocument();
  });

  it('shows the Pack picker once detection is off', () => {
    render(<UploadDialog {...makeProps({ selectedFiles: [file()], selectedPacks: [] })} />, {
      wrapper: makeWrapper(),
    });
    expect(screen.getByRole('switch', { name: /detect packs automatically/i })).not.toBeChecked();
    expect(screen.getByRole('combobox', { name: /packs/i })).toBeInTheDocument();
  });

  it('explains packs with the registered packs as examples, reviewed before extraction', async () => {
    render(<UploadDialog {...makeProps({ selectedFiles: [file()] })} />, { wrapper: makeWrapper() });
    fireEvent.mouseOver(screen.getByLabelText('What are packs?'));
    const tooltip = await screen.findByRole('tooltip');
    expect(tooltip).toHaveTextContent('for example Scientific and Legal.');
    expect(tooltip).toHaveTextContent('you review them before extraction starts.');
  });

  it('hides the Pack selector when no file is selected', () => {
    render(<UploadDialog {...makeProps({ selectedFiles: [] })} />, { wrapper: makeWrapper() });
    expect(screen.queryByRole('switch', { name: /detect packs automatically/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: /packs/i })).not.toBeInTheDocument();
  });

  it('hides the Pack selector when entity extraction is turned off', () => {
    render(
      <UploadDialog {...makeProps({ selectedFiles: [file()], extractEntities: false })} />,
      { wrapper: makeWrapper() },
    );
    expect(screen.queryByRole('switch', { name: /detect packs automatically/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: /packs/i })).not.toBeInTheDocument();
  });
});
