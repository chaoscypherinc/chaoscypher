// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * @module DomainSelect
 * Lean extraction-domain selector. Each row shows icon + name only; the
 * description, "Built-in" marker, and token cost live in a hover tooltip. A
 * single ⚠ marker stays on the row only when a domain would overflow the
 * model's context window (utilization > 90%). Promoted to the top level of the
 * Add Source dialog and the Confirm extraction dialog, so it's visible the
 * moment a file is added rather than buried in "Advanced".
 */

import { useCallback, type ReactNode } from 'react';
import { Box, FormControl, InputLabel, Select, MenuItem, Tooltip } from '@mui/material';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import { accentSelectSx } from '../../theme/accentStyles';
import { getMuiIcon } from '../../utils/icons';
import type { ExtractionDomain } from '../../services/api/sources';

const AUTO_VALUE = '__auto__';
const OVER_BUDGET_PCT = 90;
const OVER_BUDGET_TITLE = 'Exceeds the context window';

/** Fixed per-request overhead (system prompt, schema, etc.) by window size. */
function getBaseOverhead(contextWindow: number): number {
  if (contextWindow <= 4096) return 1500;
  if (contextWindow <= 8192) return 2000;
  return 2500;
}

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

interface DomainSelectProps {
  selectedDomain: string;
  availableDomains: ExtractionDomain[];
  onDomainChange: (value: string) => void;
  contextWindow: number;
  groupSize: number;
  inputPerChunk: number;
  outputPerChunk: number;
}

export function DomainSelect({
  selectedDomain,
  availableDomains,
  onDomainChange,
  contextWindow,
  groupSize,
  inputPerChunk,
  outputPerChunk,
}: DomainSelectProps) {
  const usageFor = useCallback(
    (domain: ExtractionDomain): { tokens: number; pct: number } => {
      const tokens =
        getBaseOverhead(contextWindow) +
        (domain.prompt_tokens || 0) +
        groupSize * inputPerChunk +
        groupSize * outputPerChunk;
      return { tokens, pct: Math.round((tokens / contextWindow) * 100) };
    },
    [contextWindow, groupSize, inputPerChunk, outputPerChunk],
  );

  // The closed control renders icon + name only (no Tooltip wrapper) so hovering
  // the collapsed select doesn't pop a tooltip.
  const renderValue = useCallback(
    (value: string): ReactNode => {
      if (value === AUTO_VALUE) {
        return (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <AutoAwesomeIcon sx={{ fontSize: 18, color: 'warning.main' }} />
            Auto (Recommended)
          </Box>
        );
      }
      const domain = availableDomains.find((d) => d.name === value);
      const DomainIcon = getMuiIcon(domain?.icon);
      return (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <DomainIcon sx={{ fontSize: 18, color: 'text.secondary' }} />
          {domain ? capitalize(domain.name) : value}
        </Box>
      );
    },
    [availableDomains],
  );

  return (
    <FormControl fullWidth size="small" variant="outlined" sx={accentSelectSx('domain')}>
      <InputLabel id="domain-select-label">Domain</InputLabel>
      <Select
        labelId="domain-select-label"
        value={selectedDomain}
        label="Domain"
        renderValue={renderValue}
        onChange={(e) => onDomainChange(e.target.value)}
      >
        <MenuItem value={AUTO_VALUE}>
          <Tooltip
            placement="right"
            arrow
            title={
              <Box sx={{ fontSize: '0.75rem', lineHeight: 1.5 }}>
                Automatically detect the best extraction domain based on content. You&apos;ll be
                asked to confirm before extraction runs.
              </Box>
            }
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, width: '100%' }}>
              <AutoAwesomeIcon sx={{ fontSize: 18, color: 'warning.main' }} />
              Auto (Recommended)
            </Box>
          </Tooltip>
        </MenuItem>
        {availableDomains.map((domain) => {
          const DomainIcon = getMuiIcon(domain.icon);
          const { tokens, pct } = usageFor(domain);
          const overBudget = pct > OVER_BUDGET_PCT;
          return (
            <MenuItem key={domain.name} value={domain.name}>
              <Tooltip
                placement="right"
                arrow
                title={
                  <Box sx={{ fontSize: '0.75rem', lineHeight: 1.5 }}>
                    <Box sx={{ fontWeight: 700 }}>{capitalize(domain.name)}</Box>
                    <Box>{domain.description}</Box>
                    <Box sx={{ mt: 0.5, opacity: 0.85 }}>
                      {domain.builtin ? 'Built-in · ' : ''}~{(tokens / 1000).toFixed(1)}k tokens ({pct}%)
                    </Box>
                  </Box>
                }
              >
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, width: '100%' }}>
                  <DomainIcon sx={{ fontSize: 18, color: 'text.secondary' }} />
                  {capitalize(domain.name)}
                  {overBudget && (
                    <WarningAmberIcon
                      titleAccess={OVER_BUDGET_TITLE}
                      sx={{ fontSize: 16, color: 'warning.main', ml: 'auto' }}
                    />
                  )}
                </Box>
              </Tooltip>
            </MenuItem>
          );
        })}
      </Select>
    </FormControl>
  );
}
