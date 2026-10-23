// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * `sitesApi.create`: a site import names the pages to leave out, never a
 * list of the pages to take, so a capped site check never drops the pages
 * it did not see.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../client', () => ({
  apiClient: {
    post: vi.fn(),
  },
}));

import { apiClient } from '../client';
import { sitesApi } from '../sites';
import type { UrlImportOptions } from '../../../types';

const post = apiClient.post as ReturnType<typeof vi.fn>;

const OPTIONS: UrlImportOptions = {
  extract_entities: true,
  analysis_depth: 'full',
  enable_vision: true,
  content_filtering: true,
  skip_duplicates: false,
  auto_confirm: true,
};

describe('sitesApi.create', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    post.mockResolvedValue({ data: { id: 'site-1' } });
  });

  it('sends the unticked pages as exclusions', async () => {
    await sitesApi.create('https://docs.example.com', ['https://docs.example.com/a'], OPTIONS);

    expect(post).toHaveBeenCalledWith('/sources/sites', {
      url: 'https://docs.example.com',
      exclude: ['https://docs.example.com/a'],
      options: OPTIONS,
    });
  });

  it('sends no exclusions when every page is taken', async () => {
    await sitesApi.create('https://docs.example.com', [], OPTIONS);

    expect(post).toHaveBeenCalledWith('/sources/sites', {
      url: 'https://docs.example.com',
      exclude: null,
      options: OPTIONS,
    });
  });
});
