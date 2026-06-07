// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Routes, Route } from 'react-router';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { installFetchMock } from '../../test/mocks/fetch';
import { makeWrapper } from '../../test/renderWithProviders';
import { apiClient } from '../../services/api/client';
import ChatPage from '../ChatPage';

vi.mock('../../services/api/client', () => installApiClientMock());

describe('ChatPage', () => {
  beforeEach(() => {
    installFetchMock();
    // chatApi.listChats unwraps `response.data.data` from the paginated
    // envelope, so the generic `{ data: {} }` default in the mock client
    // yields `undefined` and breaks ChatHeaderBar at render time. Supply a
    // properly-shaped paginated response for the listChats GET.
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { data: [], pagination: { page: 1, page_size: 50, total_items: 0, total_pages: 0 } },
    } as never);
  });

  it('renders the empty-state welcome heading and new-chat control', async () => {
    render(
      <Routes>
        <Route path="/chat" element={<ChatPage />} />
      </Routes>,
      { wrapper: makeWrapper({ initialEntries: ['/chat'] }) },
    );

    // With the mocked empty chat list (no messages), ChatMessageList renders
    // its empty state: a static "AI Research Assistant" h5 heading. This is
    // proof the page mounted the message area rather than a blank/error
    // screen — a heading that only exists on this page in this state.
    expect(
      await screen.findByRole('heading', { name: /ai research assistant/i }),
    ).toBeInTheDocument();

    // The ChatHeaderBar always exposes the icon-only "New chat" button; its
    // presence proves the header composed correctly with the mocked chats.
    expect(
      screen.getByRole('button', { name: /new chat/i }),
    ).toBeInTheDocument();
  });
});
