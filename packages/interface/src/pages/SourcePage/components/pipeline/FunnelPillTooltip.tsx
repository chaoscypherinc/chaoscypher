// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { Box, Typography } from '@mui/material';

export interface FunnelPillTooltipProps {
  title: string;
  explanation: string;
  dataLines: string[];
  footerHint: string;
}

export function FunnelPillTooltip({
  title,
  explanation,
  dataLines,
  footerHint,
}: FunnelPillTooltipProps) {
  return (
    <Box sx={{ minWidth: 220, p: 0.5 }}>
      <Typography
        sx={{ fontWeight: 600, fontSize: '0.78rem', color: '#fff', mb: 0.5 }}
      >
        {title}
      </Typography>
      <Typography sx={{ fontSize: '0.7rem', color: '#bbb', lineHeight: 1.45, mb: 0.75 }}>
        {explanation}
      </Typography>
      {dataLines.length > 0 && (
        <Box
          data-testid="tooltip-data-lines"
          sx={{
            borderTop: '1px dashed rgba(255,255,255,0.15)',
            pt: 0.75,
            mb: 0.75,
            fontFamily: 'ui-monospace, monospace',
            fontSize: '0.7rem',
            color: '#fff',
          }}
        >
          {dataLines.map((line) => (
            <Box key={line}>{line}</Box>
          ))}
        </Box>
      )}
      <Typography sx={{ fontSize: '0.62rem', color: '#888', fontStyle: 'italic' }}>
        {footerHint}
      </Typography>
    </Box>
  );
}
