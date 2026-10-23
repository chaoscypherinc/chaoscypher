// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * SiteRow: an imported site as one row of the sources table, with its
 * progress ("43 of 71 pages" and a bar), its run status, "Check for updates"
 * (disabled, with the reason, while a run is queued or under way), delete
 * (keeping or deleting the pages), and its pages on expanding, gone ones
 * marked, and the pages it leaves out (a check keeps them out; "Include the
 * left-out pages" brings them back). Mocks at the apiClient layer, so the real services and query hooks
 * run.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { Table, TableBody } from '@mui/material';
import { installApiClientMock } from '../../../../test/mocks/apiClient';
import { makeWrapper } from '../../../../test/renderWithProviders';
import { apiClient } from '../../../../services/api/client';
import { SiteRow } from '../SiteRow';
import { SourceRow } from '../SourceRow';
import type { Site, UnifiedSource } from '../../../../types';

vi.mock('../../../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

function makeSite(overrides: Partial<Site> = {}): Site {
  return {
    id: 'site-1',
    url: 'https://docs.example.com',
    title: 'Example Docs',
    discovery_method: 'llms_txt',
    status: 'imported',
    run_in_progress: false,
    page_count: 71,
    options: {},
    error_message: null,
    created_at: '2026-10-22T10:00:00Z',
    last_checked_at: '2026-10-22T10:30:00Z',
    progress: {
      total: 71,
      done: 71,
      failed: 0,
      awaiting_confirmation: 0,
      in_progress: 0,
      gone: 0,
      by_status: { committed: 71 },
    },
    excluded_pages: [],
    ...overrides,
  };
}

const IMPORTING_PROGRESS: Site['progress'] = {
  total: 71,
  done: 43,
  failed: 2,
  awaiting_confirmation: 0,
  in_progress: 26,
  gone: 0,
  by_status: { committed: 43, error: 2, extracting: 26 },
};

function renderRow(site: Site) {
  const renderPage = (page: UnifiedSource) => (
    <SourceRow
      key={page.id}
      source={page}
      isSelected={false}
      anySelected={false}
      onSelectionChange={vi.fn()}
      onRowClick={vi.fn()}
      onMenuOpen={vi.fn()}
    />
  );
  return render(
    <Table>
      <TableBody>
        <SiteRow site={site} pollInterval={3000} renderPage={renderPage} />
      </TableBody>
    </Table>,
    { wrapper: makeWrapper() },
  );
}

async function openMenu() {
  fireEvent.click(screen.getByRole('button', { name: 'Site actions' }));
  return screen.findByRole('menu');
}

describe('<SiteRow />', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows the site as one row: title, address, pages done of total, a bar, its status', () => {
    renderRow(makeSite({ status: 'importing', progress: IMPORTING_PROGRESS }));

    expect(screen.getByText('Example Docs')).toBeInTheDocument();
    expect(screen.getByText('https://docs.example.com')).toBeInTheDocument();
    expect(screen.getByText('43 of 71 pages')).toBeInTheDocument();
    const bar = screen.getByRole('progressbar', { name: 'Example Docs progress' });
    expect(bar).toHaveAttribute('aria-valuenow', '61');
    expect(screen.getByText('Importing')).toBeInTheDocument();
    expect(screen.getByText('2 failed')).toBeInTheDocument();
  });

  it('says when the site is still being searched for pages', () => {
    renderRow(
      makeSite({
        status: 'importing',
        run_in_progress: true,
        page_count: 0,
        progress: { ...makeSite().progress, total: 0, done: 0 },
      }),
    );
    expect(screen.getByText('Finding pages…')).toBeInTheDocument();
  });

  it('shows a failed run with its reason', () => {
    renderRow(makeSite({ status: 'failed', error_message: 'No pages were found under this address.' }));
    expect(screen.getByText('Failed')).toBeInTheDocument();
    expect(screen.getByText('No pages were found under this address.')).toBeInTheDocument();
  });

  it.each([
    ['queued', 'Waiting to start'],
    ['importing', 'The import is still running'],
    ['refreshing', 'A check is already running'],
  ] as const)(
    'disables Check for updates and Delete while the site is %s, and says why',
    async (status, reason) => {
      renderRow(makeSite({ status, run_in_progress: true }));

      const menu = await openMenu();
      const item = within(menu).getByRole('menuitem', { name: /Check for updates/ });
      expect(item).toHaveAttribute('aria-disabled', 'true');
      expect(within(item).getByText(reason)).toBeInTheDocument();
      // The run would die on a deleted site: the delete waits for it too.
      const remove = within(menu).getByRole('menuitem', { name: /Delete site/ });
      expect(remove).toHaveAttribute('aria-disabled', 'true');
      expect(within(remove).getByText(reason)).toBeInTheDocument();
    },
  );

  it('leaves Check for updates and Delete open when a run went stale', async () => {
    // A killed worker left the site "importing"; the backend says no run is live.
    renderRow(makeSite({ status: 'importing', run_in_progress: false }));

    const menu = await openMenu();
    const item = within(menu).getByRole('menuitem', { name: /Check for updates/ });
    expect(item).not.toHaveAttribute('aria-disabled');
    expect(within(menu).getByRole('menuitem', { name: /Delete site/ })).not.toHaveAttribute(
      'aria-disabled',
    );
  });

  it('checks an imported site for updates', async () => {
    mockedApiClient.post.mockResolvedValue({ data: makeSite({ status: 'queued' }) });
    renderRow(makeSite());

    const menu = await openMenu();
    fireEvent.click(within(menu).getByRole('menuitem', { name: /Check for updates/ }));

    await waitFor(() =>
      expect(mockedApiClient.post).toHaveBeenCalledWith('/sources/sites/site-1/refreshes'),
    );
  });

  it('says how many pages the site leaves out, and names a few on hover or focus', async () => {
    const many = Array.from({ length: 8 }, (_, n) => `https://docs.example.com/p${n}`);
    renderRow(makeSite({ excluded_pages: many }));

    const note = screen.getByText('8 pages left out');
    // Reachable from the keyboard, and the tooltip describes it without renaming it.
    expect(note).toHaveAttribute('tabindex', '0');
    expect(note).not.toHaveAttribute('aria-label');
    // Reached with the keyboard: a key press, then focus.
    fireEvent.keyDown(document.body, { key: 'Tab' });
    act(() => note.focus());
    const tooltip = await screen.findByRole('tooltip');
    expect(tooltip).toHaveTextContent(`${many.slice(0, 5).join(', ')} and 3 more`);
    expect(tooltip).not.toHaveTextContent('p7');
  });

  it('keeps the left-out pages out on a check, and asks before bringing them back', async () => {
    mockedApiClient.post.mockResolvedValue({ data: makeSite({ status: 'queued' }) });
    renderRow(makeSite({ excluded_pages: ['https://docs.example.com/faq'] }));

    let menu = await openMenu();
    fireEvent.click(within(menu).getByRole('menuitem', { name: /Check for updates/ }));
    // No list sent: the site's own stays.
    await waitFor(() =>
      expect(mockedApiClient.post).toHaveBeenCalledWith('/sources/sites/site-1/refreshes'),
    );
    mockedApiClient.post.mockClear();

    menu = await openMenu();
    fireEvent.click(within(menu).getByRole('menuitem', { name: /Include the left-out page/ }));
    let dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Bring back 1 page?')).toBeInTheDocument();
    expect(dialog).toHaveTextContent('including any you deleted from the site');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(mockedApiClient.post).not.toHaveBeenCalled();

    menu = await openMenu();
    fireEvent.click(within(menu).getByRole('menuitem', { name: /Include the left-out page/ }));
    dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Bring back' }));
    await waitFor(() =>
      expect(mockedApiClient.post).toHaveBeenCalledWith('/sources/sites/site-1/refreshes', {
        exclude: [],
      }),
    );
  });

  it('offers no include when the site leaves nothing out', async () => {
    renderRow(makeSite());
    expect(screen.queryByText(/left out/)).not.toBeInTheDocument();
    const menu = await openMenu();
    expect(within(menu).queryByRole('menuitem', { name: /Include the left-out/ })).toBeNull();
  });

  it('deletes the site, keeping its pages unless told otherwise', async () => {
    mockedApiClient.delete.mockResolvedValue({ data: undefined });
    renderRow(makeSite());

    const menu = await openMenu();
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Delete site' }));
    const dialog = await screen.findByRole('dialog');
    expect(
      within(dialog).getByRole('radio', { name: 'Keep its 71 pages as separate sources' }),
    ).toBeChecked();

    fireEvent.click(within(dialog).getByRole('radio', { name: 'Delete its 71 pages too' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() =>
      expect(mockedApiClient.delete).toHaveBeenCalledWith('/sources/sites/site-1', {
        params: { delete_pages: true },
      }),
    );
  });

  it('expands to its pages, the existing source rows, with gone pages marked', async () => {
    mockedApiClient.get.mockResolvedValue({
      data: {
        data: [
          {
            id: 'p1',
            title: 'Introduction',
            status: 'committed',
            created_at: '2026-10-22T10:01:00Z',
            site_id: 'site-1',
            site_gone_at: null,
          },
          {
            id: 'p2',
            title: 'Old page',
            status: 'committed',
            created_at: '2026-10-22T10:02:00Z',
            site_id: 'site-1',
            site_gone_at: '2026-10-22T11:00:00Z',
          },
          {
            id: 'p3',
            title: 'Left-out page',
            status: 'committed',
            created_at: '2026-10-22T10:03:00Z',
            site_id: 'site-1',
            site_gone_at: null,
            site_left_out: true,
          },
        ],
        pagination: { total: 3, page: 1, page_size: 500, total_pages: 1, has_next: false, has_prev: false },
      },
    });
    renderRow(makeSite({ progress: { ...makeSite().progress, gone: 1 } }));
    expect(screen.getByText('1 gone from the site')).toBeInTheDocument();
    // Collapsed, the pages are not fetched.
    expect(mockedApiClient.get).not.toHaveBeenCalledWith(
      '/sources/sites/site-1/pages',
      expect.anything(),
    );

    fireEvent.click(screen.getByRole('button', { name: 'Show pages' }));

    expect(await screen.findByText('Introduction')).toBeInTheDocument();
    expect(screen.getByText('Old page')).toBeInTheDocument();
    expect(mockedApiClient.get).toHaveBeenCalledWith('/sources/sites/site-1/pages', {
      params: { page_size: 500 },
    });
    expect(screen.getAllByText('Gone from site')).toHaveLength(1);
    // A page the site leaves out keeps what it has; a check does not check it.
    expect(screen.getAllByText('Left out')).toHaveLength(1);
  });
});
