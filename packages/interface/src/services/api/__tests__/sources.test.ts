// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Through the real client: a tag filter goes out as the repeated
 * `tags=a&tags=b` FastAPI reads, never as `tags[]=` or `tags[0]=`; a 422 on a
 * tag save carries the tag rule's words as the error's message.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import { sourcesApi, tagsApi } from '../sources';

const EMPTY_PAGE = {
  data: [],
  pagination: { total: 0, page: 1, page_size: 50, total_pages: 0, has_next: false, has_prev: false },
};

describe('sourcesApi.list', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify(EMPTY_PAGE), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('serialises repeated tags', async () => {
    await sourcesApi.list({ tags: ['alpha', 'beta'], match: 'all' });

    const url = String(fetchMock.mock.calls[0][0]);
    const query = url.slice(url.indexOf('?') + 1);
    expect(query.split('&')).toEqual(expect.arrayContaining(['tags=alpha', 'tags=beta', 'match=all']));
    expect(url).toContain('tags=alpha&tags=beta');
    expect(url).not.toContain('tags%5B');
  });
});

describe('tagsApi.create', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("carries a 422's tag rule message as the error message", async () => {
    const rule = 'tag "Work Docs" is not allowed: use a-z, 0-9, \':\', \'_\' or \'-\', starting with a letter or digit, at most 64 characters';
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: 'VALIDATION_FAILED',
            message: 'Request body failed validation',
            details: { errors: [{ type: 'tag_rule', loc: ['body', 'name'], msg: rule }] },
          }),
          { status: 422, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    );

    await expect(tagsApi.create({ name: 'Work Docs' })).rejects.toThrow(rule);
  });
});
