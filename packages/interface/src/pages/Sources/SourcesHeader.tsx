// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, Typography, IconButton, Button, Tooltip } from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import { useLLMHealth } from '../../hooks/useLLMHealth';

interface SourcesHeaderProps {
  loading: boolean;
  uploading: boolean;
  onRefresh: () => void;
  onUploadClick: () => void;
}

export function SourcesHeader({
  loading,
  uploading,
  onRefresh,
  onUploadClick,
}: SourcesHeaderProps) {
  const { data: health } = useLLMHealth();
  const verified = health?.verified === true;
  const missingModels = health?.missing_models ?? [];
  const llmReady = verified && missingModels.length === 0;
  const uploadDisabled = uploading || !llmReady;

  let tooltip = '';
  if (!verified) {
    tooltip = 'Configure and verify your LLM in Settings to enable import';
  } else if (missingModels.length > 0) {
    tooltip =
      `Configured model${missingModels.length > 1 ? 's' : ''} not pulled: ` +
      `${missingModels.join(', ')}. Open Settings → LLM and pull, then retry.`;
  }

  return (
    <Box
      sx={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: 2,
        justifyContent: 'space-between',
        alignItems: { xs: 'flex-start', sm: 'center' },
        mb: 3,
      }}
    >
      <Typography variant="h4">Sources</Typography>
      <Box sx={{ display: 'flex', gap: 2, alignItems: 'center' }}>
        <IconButton aria-label="Refresh" onClick={onRefresh} disabled={loading}>
          <RefreshIcon />
        </IconButton>
        <Tooltip title={tooltip} placement="bottom">
          <span>
            <Button
              variant="outlined"
              startIcon={<UploadFileIcon />}
              onClick={onUploadClick}
              disabled={uploadDisabled}
              sx={{
                borderColor: 'rgba(0, 229, 255, 0.3)',
                color: 'primary.main',
                bgcolor: 'transparent',
                '&:hover': {
                  borderColor: 'rgba(0, 229, 255, 0.5)',
                  bgcolor: 'rgba(0, 229, 255, 0.05)',
                },
              }}
            >
              Add Source
            </Button>
          </span>
        </Tooltip>
      </Box>
    </Box>
  );
}
