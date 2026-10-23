// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { makeWrapper } from '../../../test/renderWithProviders';
import { settingsApi } from '../../../services/api/settings';
import type { Settings, SettingsUpdateResponse } from '../../../types';
import GraphRagAccordion, { UNEVIDENCED_LABEL } from '../GraphRagAccordion';

vi.mock('../../../services/api/settings', () => ({
  settingsApi: { get: vi.fn(), update: vi.fn() },
}));

const api = vi.mocked(settingsApi);

function settingsWith(includeUnevidenced: boolean): Settings {
  return { graphrag: { include_unevidenced_edges: includeUnevidenced, max_triples: 40 } } as unknown as Settings;
}

async function renderExpanded() {
  const user = userEvent.setup();
  render(<GraphRagAccordion />, { wrapper: makeWrapper() });
  await user.click(screen.getByRole('button', { name: /chat context/i }));
  return user;
}

describe('GraphRagAccordion', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.get.mockResolvedValue(settingsWith(false));
  });

  it('shows the switch off by default, with help text saying what an unevidenced edge is', async () => {
    await renderExpanded();
    const toggle = await screen.findByRole('switch', { name: UNEVIDENCED_LABEL });
    await waitFor(() => expect(toggle).toBeEnabled());
    expect(toggle).not.toBeChecked();
    expect(screen.getByText(UNEVIDENCED_LABEL)).toBeInTheDocument();
    expect(screen.getByTestId('unevidenced-help')).toHaveTextContent(
      /no sentence it cites actually states/i,
    );
  });

  it('turning it on PATCHes the graphrag section and shows the saved value', async () => {
    api.update.mockResolvedValue({
      settings: settingsWith(true),
      warnings: [],
    } as SettingsUpdateResponse);
    const user = await renderExpanded();
    const toggle = await screen.findByRole('switch', { name: UNEVIDENCED_LABEL });
    await waitFor(() => expect(toggle).toBeEnabled());

    await user.click(toggle);

    expect(api.update).toHaveBeenCalledWith({ graphrag: { include_unevidenced_edges: true } });
    await waitFor(() => expect(toggle).toBeChecked());
  });

  it('surfaces a failed save', async () => {
    api.update.mockRejectedValue(new Error('boom'));
    const user = await renderExpanded();
    const toggle = await screen.findByRole('switch', { name: UNEVIDENCED_LABEL });
    await waitFor(() => expect(toggle).toBeEnabled());
    await user.click(toggle);
    expect(await screen.findByRole('alert')).toBeInTheDocument();
  });
});
