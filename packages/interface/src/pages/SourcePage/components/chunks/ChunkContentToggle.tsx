// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
// ChunkContentToggle.tsx
import { ToggleButton, ToggleButtonGroup, Tooltip } from '@mui/material';

export interface ChunkContentToggleProps {
  view: 'input' | 'output';
  outputAvailable: boolean;
  onChange: (next: 'input' | 'output') => void;
}

export function ChunkContentToggle({ view, outputAvailable, onChange }: ChunkContentToggleProps) {
  return (
    <ToggleButtonGroup
      exclusive
      size="small"
      value={view}
      onChange={(_, next: 'input' | 'output' | null) => {
        if (next && next !== view) onChange(next);
      }}
      sx={{ '& .MuiToggleButton-root': { fontSize: '0.7rem', px: 1.5, py: 0.5 } }}
    >
      <ToggleButton value="input">INPUT</ToggleButton>
      <Tooltip
        title={outputAvailable ? '' : "Extraction hasn't produced output for this chunk yet."}
        arrow
        placement="top"
        disableHoverListener={outputAvailable}
      >
        <span>
          <ToggleButton value="output" disabled={!outputAvailable}>
            OUTPUT
          </ToggleButton>
        </span>
      </Tooltip>
    </ToggleButtonGroup>
  );
}
