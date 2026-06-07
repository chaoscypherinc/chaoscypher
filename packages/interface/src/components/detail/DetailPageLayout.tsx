// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box } from '@mui/material';
import type { ReactNode } from 'react';

interface DetailPageLayoutProps {
  /** Main content area (wider column). */
  main: ReactNode;
  /** Right sidebar area (minWidth 280, flex 1). */
  sidebar: ReactNode;
  /**
   * Flex weight of the main column relative to the sidebar (default: 3).
   * TemplateDetailPage overrides to 2 for its wider sidebar proportions.
   */
  mainFlex?: number;
}

/**
 * Shared two-column layout for detail pages: wide main content on the left,
 * narrow sidebar on the right. Wraps on small screens.
 */
export default function DetailPageLayout({
  main,
  sidebar,
  mainFlex = 3,
}: DetailPageLayoutProps) {
  return (
    <Box sx={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>
      <Box sx={{ flex: mainFlex, minWidth: 0 }}>{main}</Box>
      <Box sx={{ flex: 1, minWidth: 280 }}>{sidebar}</Box>
    </Box>
  );
}
