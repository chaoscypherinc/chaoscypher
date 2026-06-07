// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, Button, Typography } from '@mui/material';
import BackIcon from '@mui/icons-material/ArrowBack';
import type { ReactNode } from 'react';
import { ghostCancelBtnSx } from '../../theme/ghostStyles';

interface DetailPageHeaderProps {
  /** Title text (typically "Edit X" in edit mode, entity label otherwise). */
  title: string;
  /** Icon displayed to the left of the title (typically a <TemplateIcon>). */
  icon: ReactNode;
  /** Click handler for the back button. */
  onBack: () => void;
  /** Back button label (default: "Back"). */
  backLabel?: string;
  /** Optional content to the right of the title (e.g., a badge chip). */
  titleSuffix?: ReactNode;
  /** Action buttons on the right of the header. */
  actions: ReactNode;
}

/**
 * Shared header for detail pages: back button, icon, title, optional suffix,
 * and a slot for action buttons on the right.
 */
export default function DetailPageHeader({
  title,
  icon,
  onBack,
  backLabel = 'Back',
  titleSuffix,
  actions,
}: DetailPageHeaderProps) {
  return (
    <Box
      sx={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        mb: 3,
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
        <Button startIcon={<BackIcon />} onClick={onBack} sx={ghostCancelBtnSx}>
          {backLabel}
        </Button>
        {icon}
        <Typography variant="h4">{title}</Typography>
        {titleSuffix}
      </Box>
      <Box sx={{ display: 'flex', gap: 1 }}>{actions}</Box>
    </Box>
  );
}
