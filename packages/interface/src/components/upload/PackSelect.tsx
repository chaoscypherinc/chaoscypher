// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * @module PackSelect
 * The packs a source extracts with, on top of the floor every source has
 * (spec 2026-10-07 § 5): an MUI `Autocomplete multiple` with one chip per pack.
 * The detection proposal arrives pre-selected (its chips filled), any pack can
 * be added or removed, and no chip means the floor alone (the eight core types
 * and the core relationships). At upload (`allowAuto`) a switch leaves the
 * choice to detection instead; the confirm step then shows the proposal.
 *
 * Each option shows its icon and name; the description and the prompt cost
 * live in a hover tooltip. The helper line carries the selection's estimated
 * prompt size and warns when it would overflow the model's context window.
 */

import { createElement, useCallback, useMemo, type ReactNode } from 'react';
import {
  Autocomplete,
  Box,
  Chip,
  FormControlLabel,
  Switch,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import { accentSelectSx } from '../../theme/accentStyles';
import { ghostSwitchSx } from '../../theme/ghostStyles';
import { getMuiIcon } from '../../utils/icons';
import type { ExtractionDomain } from '../../services/api/sourceProcessing';
import { FLOOR_DOMAIN, capitalizePack, packsLabel, packsOf } from '../../utils/packs';

const OVER_BUDGET_PCT = 90;

/** Fixed per-request overhead (system prompt, schema, etc.) by window size. */
function getBaseOverhead(contextWindow: number): number {
  if (contextWindow <= 4096) return 1500;
  if (contextWindow <= 8192) return 2000;
  return 2500;
}

interface PackSelectProps {
  /** The chosen packs; `[]` is the floor alone; `null` leaves it to detection (`allowAuto`). */
  value: string[] | null;
  availableDomains: ExtractionDomain[];
  onChange: (packs: string[] | null) => void;
  /** Offer "detect automatically" (upload); the confirm step and source settings do not. */
  allowAuto?: boolean;
  /** The packs detection proposed: their chips are drawn filled. */
  proposed?: string[];
  disabled?: boolean;
  /** Capacity inputs for the prompt-size estimate (all optional). */
  contextWindow?: number;
  groupSize?: number;
  inputPerChunk?: number;
  outputPerChunk?: number;
}

export function PackSelect({
  value,
  availableDomains,
  onChange,
  allowAuto = false,
  proposed = [],
  disabled = false,
  contextWindow = 8192,
  groupSize = 4,
  inputPerChunk = 150,
  outputPerChunk = 2000,
}: PackSelectProps) {
  const packs = useMemo(() => packsOf(availableDomains), [availableDomains]);
  const byName = useMemo(() => new Map(packs.map((p) => [p.name, p])), [packs]);
  const floor = useMemo(
    () => availableDomains.find((d) => d.role === 'floor' || d.name === FLOOR_DOMAIN),
    [availableDomains],
  );
  const auto = allowAuto && value === null;
  const selected = useMemo(() => (value ?? []).filter((n) => byName.has(n)), [value, byName]);

  const usage = useMemo(() => {
    const promptTokens =
      (floor?.prompt_tokens ?? 0) +
      selected.reduce((sum, name) => sum + (byName.get(name)?.prompt_tokens ?? 0), 0);
    const tokens =
      getBaseOverhead(contextWindow) + promptTokens + groupSize * (inputPerChunk + outputPerChunk);
    return { tokens, pct: Math.round((tokens / contextWindow) * 100) };
  }, [floor, selected, byName, contextWindow, groupSize, inputPerChunk, outputPerChunk]);

  const iconFor = useCallback(
    (name: string, color = 'text.secondary') =>
      createElement(getMuiIcon(byName.get(name)?.icon), {
        sx: { fontSize: 16, color: `${color} !important` },
      }),
    [byName],
  );

  let helper: ReactNode;
  if (auto) {
    helper = 'Detection proposes up to two packs; you confirm them before extraction runs.';
  } else if (selected.length === 0) {
    helper = 'Floor only: the eight core types and the core relationships, no pack.';
  } else {
    helper = `Floor + ${packsLabel(selected)} · ~${(usage.tokens / 1000).toFixed(1)}k tokens (${usage.pct}%)`;
  }
  const overBudget = !auto && usage.pct > OVER_BUDGET_PCT;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
      {allowAuto && (
        <FormControlLabel
          control={
            <Switch
              size="small"
              checked={auto}
              disabled={disabled}
              onChange={(e) => onChange(e.target.checked ? null : [])}
              sx={ghostSwitchSx}
            />
          }
          label={<Typography variant="body2">Detect packs automatically</Typography>}
        />
      )}
      <Autocomplete
        multiple
        size="small"
        disabled={disabled || auto}
        options={packs.map((p) => p.name)}
        value={selected}
        onChange={(_e, next) => onChange(next)}
        filterSelectedOptions
        getOptionLabel={capitalizePack}
        renderValue={(chosen, getItemProps) =>
          chosen.map((name, index) => {
            const { key, ...itemProps } = getItemProps({ index });
            const isProposed = proposed.includes(name);
            return (
              <Chip
                key={key}
                {...itemProps}
                size="small"
                icon={iconFor(name)}
                label={capitalizePack(name)}
                variant={isProposed ? 'filled' : 'outlined'}
                title={isProposed ? 'Proposed by detection' : undefined}
              />
            );
          })
        }
        renderOption={(props, name) => {
          const { key, ...optionProps } = props;
          const pack = byName.get(name);
          return (
            <li key={key} {...optionProps}>
              <Tooltip
                placement="right"
                arrow
                title={
                  <Box sx={{ fontSize: '0.75rem', lineHeight: 1.5 }}>
                    <Box sx={{ fontWeight: 700 }}>{capitalizePack(name)}</Box>
                    <Box>{pack?.description}</Box>
                    <Box sx={{ mt: 0.5, opacity: 0.85 }}>
                      {pack?.builtin ? 'Built-in · ' : ''}~
                      {((pack?.prompt_tokens ?? 0) / 1000).toFixed(1)}k prompt tokens
                    </Box>
                  </Box>
                }
              >
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, width: '100%' }}>
                  {iconFor(name)}
                  {capitalizePack(name)}
                </Box>
              </Tooltip>
            </li>
          );
        }}
        renderInput={(params) => (
          <TextField
            {...params}
            label="Packs"
            placeholder={selected.length === 0 && !auto ? 'Floor only' : undefined}
            sx={accentSelectSx('domain')}
          />
        )}
      />
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
        {overBudget && (
          <WarningAmberIcon
            titleAccess="Exceeds the context window"
            sx={{ fontSize: 16, color: 'warning.main' }}
          />
        )}
        <Typography variant="caption" sx={{ color: 'text.secondary' }} data-testid="pack-select-helper">
          {helper}
        </Typography>
      </Box>
    </Box>
  );
}
