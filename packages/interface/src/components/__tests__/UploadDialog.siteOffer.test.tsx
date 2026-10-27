// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * The Add Source dialog's site offer: an address typed into the URL field is
 * checked (`POST /sources/sites/previews`) once typing pauses; when the check
 * finds more than one page the dialog offers the whole site beside "Just this
 * page", with a collapsed, deselectable page list. Mocks at the apiClient
 * layer, so the real service module and query hook run.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { makeWrapper } from '../../test/renderWithProviders';
import { apiClient } from '../../services/api/client';
import {
  DEFAULT_PUBLIC_SETTINGS,
  PublicSettingsContext,
} from '../../contexts/publicSettingsContextValue';
import { UploadDialog } from '../UploadDialog';
import type { SitePreview } from '../../types';

vi.mock('../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

const SITE = 'https://docs.example.com/docs';

type Props = Parameters<typeof UploadDialog>[0];

function makeProps(overrides: Partial<Props> = {}): Props {
  return {
    open: true,
    onClose: vi.fn(),
    selectedFiles: [],
    analysisDepth: 'full',
    enableNormalization: true,
    selectedPacks: null,
    availableDomains: [],
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

function preview(overrides: Partial<SitePreview> = {}): SitePreview {
  return {
    url: SITE,
    title: 'Example Docs',
    method: 'llms_txt',
    page_count: 3,
    pages: [
      { url: `${SITE}/intro.md`, title: 'Introduction' },
      { url: `${SITE}/install.md`, title: 'Install' },
      { url: `${SITE}/api.md`, title: '' },
    ],
    capped: false,
    ...overrides,
  };
}

/** The test wrapper, with the site check's debounce at 0 so it fires at once. */
function wrapper({ children }: { children: ReactNode }) {
  const Providers = makeWrapper();
  return (
    <Providers>
      <PublicSettingsContext.Provider
        value={{ ...DEFAULT_PUBLIC_SETTINGS, web_site_preview_debounce_ms: 0 }}
      >
        {children}
      </PublicSettingsContext.Provider>
    </Providers>
  );
}

function renderDialog(props: Props) {
  render(<UploadDialog {...props} />, { wrapper });
  fireEvent.change(screen.getByLabelText('URL'), { target: { value: SITE } });
}

describe('<UploadDialog /> site offer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('checks the address and offers the whole site beside just this page', async () => {
    mockedApiClient.post.mockResolvedValue({ data: preview() });
    const props = makeProps();
    renderDialog(props);

    const whole = await screen.findByRole('radio', {
      name: 'Import the whole site: 3 pages (found by llms.txt)',
    });
    expect(mockedApiClient.post).toHaveBeenCalledWith('/sources/sites/previews', { url: SITE });
    // The safe default: one page, unless the user picks the site.
    expect(screen.getByRole('radio', { name: 'Just this page' })).toBeChecked();
    expect(whole).not.toBeChecked();

    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    await waitFor(() => expect(props.onUrlImport).toHaveBeenCalledWith(SITE));
    expect(props.onSiteImport).not.toHaveBeenCalled();
  });

  it('imports every page when the whole site is chosen', async () => {
    mockedApiClient.post.mockResolvedValue({ data: preview() });
    const props = makeProps();
    renderDialog(props);

    fireEvent.click(await screen.findByRole('radio', { name: /Import the whole site/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Import site' }));

    // Every page selected: nothing excluded; the import discovers the pages itself.
    await waitFor(() => expect(props.onSiteImport).toHaveBeenCalledWith(SITE, []));
    expect(props.onUrlImport).not.toHaveBeenCalled();
  });

  it('lists the pages collapsed, all selected, and leaves out the ones unticked', async () => {
    mockedApiClient.post.mockResolvedValue({ data: preview() });
    const props = makeProps();
    renderDialog(props);

    fireEvent.click(await screen.findByRole('radio', { name: /Import the whole site/ }));
    expect(screen.queryByRole('checkbox', { name: 'Introduction' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Show pages' }));
    const list = screen.getByRole('list', { name: 'Pages to import' });
    const boxes = within(list).getAllByRole('checkbox');
    expect(boxes).toHaveLength(3);
    boxes.forEach((box) => expect(box).toBeChecked());
    // A page without a title is named by its address.
    expect(within(list).getByRole('checkbox', { name: `${SITE}/api.md` })).toBeInTheDocument();

    fireEvent.click(within(list).getByRole('checkbox', { name: 'Install' }));
    expect(screen.getByText('2 of 3 pages selected')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Import site' }));

    await waitFor(() =>
      expect(props.onSiteImport).toHaveBeenCalledWith(SITE, [`${SITE}/install.md`]),
    );
  });

  it('unticking one page of a capped check keeps the rest of the site', async () => {
    // The quick check stopped at 3 pages of a larger site: the import must
    // still find the others, so the dialog sends what was unticked, not a list.
    mockedApiClient.post.mockResolvedValue({ data: preview({ method: 'crawl', capped: true }) });
    const props = makeProps();
    renderDialog(props);

    fireEvent.click(await screen.findByRole('radio', { name: /Import the whole site/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Show pages' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Install' }));
    fireEvent.click(screen.getByRole('button', { name: 'Import site' }));

    await waitFor(() => expect(props.onSiteImport).toHaveBeenCalledTimes(1));
    expect(props.onSiteImport).toHaveBeenCalledWith(SITE, [`${SITE}/install.md`]);
  });

  it('cannot import a site with every page deselected', async () => {
    mockedApiClient.post.mockResolvedValue({
      data: preview({ page_count: 2, pages: preview().pages.slice(0, 2) }),
    });
    renderDialog(makeProps());

    fireEvent.click(await screen.findByRole('radio', { name: /Import the whole site/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Show pages' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Introduction' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Install' }));

    expect(screen.getByRole('button', { name: 'Import site' })).toBeDisabled();
  });

  it('shows that the check is running', async () => {
    mockedApiClient.post.mockReturnValue(new Promise(() => {}));
    renderDialog(makeProps());

    expect(await screen.findByText('Looking for more pages at this address…')).toBeInTheDocument();
    // Just this page can be imported meanwhile.
    expect(screen.getByRole('button', { name: 'Import' })).toBeEnabled();
  });

  it('says so when nothing else is found, and offers no choice', async () => {
    mockedApiClient.post.mockResolvedValue({
      data: preview({ method: 'none', page_count: 0, pages: [] }),
    });
    renderDialog(makeProps());

    expect(await screen.findByText(/No other pages found/)).toBeInTheDocument();
    expect(screen.queryByRole('radio')).not.toBeInTheDocument();
  });

  it('explains a refused address and still allows the single-page import', async () => {
    mockedApiClient.post.mockRejectedValue(
      Object.assign(new Error('The site address is blocked by the URL safety policy.'), {
        isApiError: true,
        status: 400,
      }),
    );
    const props = makeProps();
    renderDialog(props);

    expect(
      await screen.findByText(/blocked by the URL safety policy/),
    ).toBeInTheDocument();
    expect(screen.queryByRole('radio')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    await waitFor(() => expect(props.onUrlImport).toHaveBeenCalledWith(SITE));
  });

  it('says when the check stopped early', async () => {
    mockedApiClient.post.mockResolvedValue({
      data: preview({ method: 'crawl', capped: true }),
    });
    renderDialog(makeProps());

    expect(
      await screen.findByRole('radio', {
        name: 'Import the whole site: at least 3 pages (found by crawling)',
      }),
    ).toBeInTheDocument();
    expect(screen.getByText(/the import finds the rest, up to 500 pages/)).toBeInTheDocument();
  });

  it('does not check an address that is not http(s)', async () => {
    render(<UploadDialog {...makeProps()} />, { wrapper });
    fireEvent.change(screen.getByLabelText('URL'), { target: { value: 'docs.example.com' } });

    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(mockedApiClient.post).not.toHaveBeenCalled();
  });

  it('says the detected packs apply straight away once the whole site is chosen', async () => {
    mockedApiClient.post.mockResolvedValue({ data: preview() });
    renderDialog(
      makeProps({
        availableDomains: [
          { name: 'generic', description: 'The floor', builtin: true, role: 'floor' },
          { name: 'technical', description: 'Software', builtin: true, role: 'pack' },
        ],
      }),
    );
    const helper = await screen.findByTestId('pack-select-helper');
    expect(helper).toHaveTextContent('you review them before extraction starts');

    fireEvent.click(await screen.findByRole('radio', { name: /Import the whole site/ }));
    expect(helper).toHaveTextContent('the pages extract with them straight away');
    fireEvent.mouseOver(screen.getByLabelText('What are packs?'));
    expect(await screen.findByRole('tooltip')).toHaveTextContent(
      'a whole-site import extracts with them straight away, without a review.',
    );
  });
});
