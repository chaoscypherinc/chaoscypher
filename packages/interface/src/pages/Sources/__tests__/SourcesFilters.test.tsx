// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useState } from 'react';
import { render, screen, fireEvent } from '@testing-library/react';

import { installApiClientMock } from '../../../test/mocks/apiClient';
import { makeWrapper } from '../../../test/renderWithProviders';
import { apiClient } from '../../../services/api/client';
import type { SourceTag, TagMatch } from '../../../types';
import { SourcesFilters } from '../SourcesFilters';
import { SOURCE_STATUS_FILTER_OPTIONS } from '../sourceStatusFilterOptions';

vi.mock('../../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

// Mirrors `components['schemas']['SourceStatus']` in the generated API types.
// The option list is typed against that schema (tsc rejects a phantom value);
// this test pins the runtime list so a regenerated schema that drops a status
// is noticed here rather than as an always-empty filter in the UI.
const SOURCE_STATUSES = [
  'pending',
  'indexing',
  'vision_pending',
  'indexed',
  'awaiting_confirmation',
  'extracting',
  'mcp_extracting',
  'extracted',
  'committing',
  'committed',
  'error',
];

describe('SourcesFilters — status options', () => {
  it('offers exactly the SourceStatus values the API matches on', () => {
    // The value is sent verbatim to `GET /sources?status=` and matched
    // exactly against the row; the select used to offer `active` and
    // `archived`, neither of which any row ever carries.
    const values = SOURCE_STATUS_FILTER_OPTIONS.map((o) => o.value);
    expect(values).toEqual(SOURCE_STATUSES);
    expect(values).not.toContain('active');
    expect(values).not.toContain('archived');
  });

  it('gives every option a human label', () => {
    for (const option of SOURCE_STATUS_FILTER_OPTIONS) {
      expect(option.label.trim().length).toBeGreaterThan(0);
    }
  });
});

describe('SourcesFilters — tags', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedApiClient.get.mockImplementation((url: string) =>
      url === '/sources/tags'
        ? Promise.resolve({ data: [makeTag('t1', 'alpha'), makeTag('t2', 'beta'), makeTag('t3', 'gamma')] })
        : Promise.resolve({ data: {} }),
    );
  });

  it('filters by tags with any and all', async () => {
    const onTagsChange = vi.fn();
    render(<Harness onTagsChange={onTagsChange} />, { wrapper: makeWrapper() });

    fireEvent.click(screen.getByRole('button', { name: /filters/i }));
    const picker = await screen.findByRole('combobox', { name: /tags/i });

    // One tag: no Any/All toggle yet.
    fireEvent.mouseDown(picker);
    fireEvent.click(await screen.findByRole('option', { name: 'alpha' }));
    expect(onTagsChange).toHaveBeenLastCalledWith(['alpha'], 'any');
    expect(screen.queryByRole('button', { name: /^all$/i })).not.toBeInTheDocument();

    // Two tags: the toggle appears, and "All" asks for every tag.
    fireEvent.mouseDown(picker);
    fireEvent.click(await screen.findByRole('option', { name: 'beta' }));
    expect(onTagsChange).toHaveBeenLastCalledWith(['alpha', 'beta'], 'any');
    fireEvent.click(screen.getByRole('button', { name: /^all$/i }));
    expect(onTagsChange).toHaveBeenLastCalledWith(['alpha', 'beta'], 'all');
  });

  it('clears the tags and the match with the other filters', async () => {
    const onTagsChange = vi.fn();
    render(<Harness onTagsChange={onTagsChange} initialTags={['alpha', 'beta']} initialMatch="all" />, {
      wrapper: makeWrapper(),
    });

    fireEvent.click(screen.getByRole('button', { name: /filter\(s\) active/i }));
    fireEvent.click(await screen.findByRole('button', { name: /clear filters/i }));
    expect(onTagsChange).toHaveBeenLastCalledWith([], 'any');
  });
});

function makeTag(id: string, name: string): SourceTag {
  return { id, database_name: 'db-a', name, created_at: '2026-11-01T00:00:00Z' };
}

interface HarnessProps {
  onTagsChange: (tags: string[], match: TagMatch) => void;
  initialTags?: string[];
  initialMatch?: TagMatch;
}

/** Holds the filter state the page would hold, so picks accumulate. */
function Harness({ onTagsChange, initialTags = [], initialMatch = 'any' }: HarnessProps) {
  const [tags, setTags] = useState<string[]>(initialTags);
  const [match, setMatch] = useState<TagMatch>(initialMatch);
  return (
    <SourcesFilters
      searchQuery=""
      onSearchChange={() => {}}
      stageFilter="all"
      statusFilter=""
      typeFilter=""
      tagFilter={tags}
      tagMatch={match}
      sortField="created_at"
      sortDirection="desc"
      onStageChange={() => {}}
      onStatusChange={() => {}}
      onTypeChange={() => {}}
      onTagsChange={(next, nextMatch) => {
        setTags(next);
        setMatch(nextMatch);
        onTagsChange(next, nextMatch);
      }}
      onSortChange={() => {}}
    />
  );
}
