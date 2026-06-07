// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeEach } from 'vitest';
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
    selectedDomain: '__auto__',
    availableDomains: DOMAINS,
    onFilesSelected: vi.fn(),
    onAnalysisDepthChange: vi.fn(),
    onNormalizationChange: vi.fn(),
    onDomainChange: vi.fn(),
    onConfirm: vi.fn(),
    onClearSelection: vi.fn(),
    onRemoveFile: vi.fn(),
    onUrlImport: vi.fn().mockResolvedValue(undefined),
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

describe('<UploadDialog /> domain selector placement', () => {
  beforeEach(() => vi.clearAllMocks());

  it('surfaces the Domain selector at the top level once a file is selected', () => {
    render(<UploadDialog {...makeProps({ selectedFiles: [file()] })} />, { wrapper: makeWrapper() });
    expect(screen.getByRole('combobox', { name: /domain/i })).toBeInTheDocument();
  });

  it('hides the Domain selector when no file is selected', () => {
    render(<UploadDialog {...makeProps({ selectedFiles: [] })} />, { wrapper: makeWrapper() });
    expect(screen.queryByRole('combobox', { name: /domain/i })).not.toBeInTheDocument();
  });

  it('hides the Domain selector when entity extraction is turned off', () => {
    render(
      <UploadDialog {...makeProps({ selectedFiles: [file()], extractEntities: false })} />,
      { wrapper: makeWrapper() },
    );
    expect(screen.queryByRole('combobox', { name: /domain/i })).not.toBeInTheDocument();
  });
});
