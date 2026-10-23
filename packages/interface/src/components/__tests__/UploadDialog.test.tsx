// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { makeWrapper } from '../../test/renderWithProviders';
import { UploadDialog } from '../UploadDialog';
import type { ExtractionDomain } from '../../services/api/sources';

const DOMAINS: ExtractionDomain[] = [
  { name: 'science', description: 'Science', builtin: true },
  { name: 'legal', description: 'Legal', builtin: true },
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
    await screen.findByRole('combobox', { name: /packs/i });
    unmount();
  });
  beforeEach(() => vi.clearAllMocks());

  it('surfaces the Pack selector at the top level once a file is selected', () => {
    render(<UploadDialog {...makeProps({ selectedFiles: [file()] })} />, { wrapper: makeWrapper() });
    expect(screen.getByRole('combobox', { name: /packs/i })).toBeInTheDocument();
  });

  it('hides the Pack selector when no file is selected', () => {
    render(<UploadDialog {...makeProps({ selectedFiles: [] })} />, { wrapper: makeWrapper() });
    expect(screen.queryByRole('combobox', { name: /packs/i })).not.toBeInTheDocument();
  });

  it('hides the Pack selector when entity extraction is turned off', () => {
    render(
      <UploadDialog {...makeProps({ selectedFiles: [file()], extractEntities: false })} />,
      { wrapper: makeWrapper() },
    );
    expect(screen.queryByRole('combobox', { name: /packs/i })).not.toBeInTheDocument();
  });
});
