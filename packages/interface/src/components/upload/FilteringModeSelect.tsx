// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * @module FilteringModeSelect
 * Lean filtering-mode selector. Each row shows icon + label only; the one-line
 * "best for…" summary and the full per-level filter spec live in the hover
 * tooltip. The empty string means "use the domain's default preset" and is
 * surfaced as the "Auto (Recommended)" sentinel row.
 */

import type { SvgIconComponent } from '@mui/icons-material';
import { Box, FormControl, InputLabel, Select, MenuItem, Tooltip } from '@mui/material';
import SecurityIcon from '@mui/icons-material/Security';
import ShieldIcon from '@mui/icons-material/Shield';
import BalanceIcon from '@mui/icons-material/Balance';
import AutoFixHighIcon from '@mui/icons-material/AutoFixHigh';
import FilterAltIcon from '@mui/icons-material/FilterAlt';
import FilterAltOffIcon from '@mui/icons-material/FilterAltOff';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import { accentSelectSx } from '../../theme/accentStyles';

const DEFAULT_VALUE = '__default__';

interface FilteringModeItem {
  value: string;
  Icon: SvgIconComponent;
  iconColor: string;
  label: string;
  secondary: string;
  tooltipLines: string[];
}

const FILTERING_MODES: FilteringModeItem[] = [
  {
    value: 'maximum', Icon: SecurityIcon, iconColor: 'error.main', label: 'Maximum (5)',
    secondary: 'All filters on, drops on mismatches. Best for noisy or OCR content.',
    tooltipLines: ['Level 5 — Maximum', 'Evidence: strict', 'Type constraints: drop on mismatch', 'Plausibility: strict (0.40)', 'Relationship limits: on', 'Orphan filter: on', 'Structural filter: on'],
  },
  {
    value: 'strict', Icon: ShieldIcon, iconColor: 'warning.main', label: 'Strict (4)',
    secondary: 'Strict evidence and type constraints. Best for legal, medical, factual documents.',
    tooltipLines: ['Level 4 — Strict', 'Evidence: strict', 'Type constraints: drop on mismatch', 'Plausibility: standard (0.30)', 'Relationship limits: on', 'Orphan filter: on', 'Structural filter: on'],
  },
  {
    value: 'balanced', Icon: BalanceIcon, iconColor: 'success.main', label: 'Balanced (3)',
    secondary: 'All filters with fall-throughs and direction correction. Best for general-purpose documents.',
    tooltipLines: ['Level 3 — Balanced', 'Evidence: standard', 'Type constraints: fall-through', 'Plausibility: standard (0.30)', 'Relationship limits: on', 'Orphan filter: on', 'Structural filter: on'],
  },
  {
    value: 'lenient', Icon: AutoFixHighIcon, iconColor: 'info.main', label: 'Lenient (2)',
    secondary: 'Forgiving evidence for pronoun-heavy prose. Best for novels, biographies, historical texts.',
    tooltipLines: ['Level 2 — Lenient', 'Evidence: narrative', 'Type constraints: fall-through', 'Plausibility: lenient (0.20)', 'Relationship limits: on', 'Orphan filter: on', 'Structural filter: on'],
  },
  {
    value: 'minimal', Icon: FilterAltIcon, iconColor: 'text.secondary', label: 'Minimal (1)',
    secondary: 'Most filters disabled, keeps nearly all LLM output. Best for high-quality documents.',
    tooltipLines: ['Level 1 — Minimal', 'Evidence: relaxed', 'Type constraints: off', 'Plausibility: off', 'Relationship limits: elevated', 'Orphan filter: off', 'Structural filter: off'],
  },
  {
    value: 'unfiltered', Icon: FilterAltOffIcon, iconColor: 'text.disabled', label: 'Unfiltered (0)',
    secondary: 'Only deduplication and index validation. No quality filters applied.',
    tooltipLines: ['Level 0 — Unfiltered', 'Evidence: off', 'Type constraints: off', 'Plausibility: off', 'Relationship limits: off', 'Orphan filter: off', 'Structural filter: off'],
  },
];

function ModeTooltip({ secondary, lines }: { secondary: string; lines: string[] }) {
  return (
    <Box sx={{ fontSize: '0.75rem', lineHeight: 1.6 }}>
      <Box sx={{ fontWeight: 700 }}>{lines[0]}</Box>
      <Box sx={{ mb: 0.5 }}>{secondary}</Box>
      {lines.slice(1).map((line) => (
        <div key={line}>{line}</div>
      ))}
    </Box>
  );
}

interface FilteringModeSelectProps {
  filteringMode: string;
  onFilteringModeChange: (value: string) => void;
}

export function FilteringModeSelect({ filteringMode, onFilteringModeChange }: FilteringModeSelectProps) {
  return (
    <FormControl fullWidth size="small" variant="outlined" sx={accentSelectSx('filtering')}>
      <InputLabel id="filtering-mode-select-label">Filtering Mode</InputLabel>
      <Select
        labelId="filtering-mode-select-label"
        value={filteringMode || DEFAULT_VALUE}
        label="Filtering Mode"
        onChange={(e) => onFilteringModeChange(e.target.value === DEFAULT_VALUE ? '' : e.target.value)}
      >
        <MenuItem value={DEFAULT_VALUE}>
          <Tooltip
            placement="right"
            arrow
            title={
              <Box sx={{ fontSize: '0.75rem', lineHeight: 1.5 }}>
                Uses the domain&apos;s configured filtering preset. Most domains use Balanced (3).
              </Box>
            }
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, width: '100%' }}>
              <AutoAwesomeIcon sx={{ fontSize: 16, color: 'primary.main' }} />
              Auto (Recommended)
            </Box>
          </Tooltip>
        </MenuItem>
        {FILTERING_MODES.map(({ value, Icon, iconColor, label, secondary, tooltipLines }) => (
          <MenuItem key={value} value={value}>
            <Tooltip placement="right" arrow title={<ModeTooltip secondary={secondary} lines={tooltipLines} />}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, width: '100%' }}>
                <Icon sx={{ fontSize: 16, color: iconColor }} />
                {label}
              </Box>
            </Tooltip>
          </MenuItem>
        ))}
      </Select>
    </FormControl>
  );
}
