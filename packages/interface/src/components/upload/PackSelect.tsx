// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * @module PackSelect
 * The packs a source extracts with, on top of the floor every source has
 * (spec 2026-10-07 § 5): an MUI `Autocomplete multiple` with one chip per pack.
 * The detection proposal arrives pre-selected (its chips filled), any pack can
 * be added or removed, and no chip means the floor alone (the eight core types
 * and the core relationships). At upload (`allowAuto`) a switch leaves the
 * choice to detection instead; while it is on the picker is hidden (there is
 * nothing to pick), and an info icon beside it explains what packs are, with
 * built-in packs from the registered list as examples. The confirm step then
 * shows the proposal; a whole-site import (`proposalReviewed={false}`)
 * extracts each page with its detected packs at once, and the copy says so.
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
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import { accentSelectSx } from '../../theme/accentStyles';
import { ghostSwitchSx } from '../../theme/ghostStyles';
import { getMuiIcon } from '../../utils/icons';
import type { ExtractionDomain } from '../../services/api/sourceProcessing';
import { FLOOR_DOMAIN, capitalizePack, packsLabel, packsOf } from '../../utils/packs';

const OVER_BUDGET_PCT = 90;
/** How many packs the explainer names as examples. */
const EXAMPLE_PACKS = 3;

/** `A`, `A and B`, `A, B and C`. */
function joinNames(names: string[]): string {
  return names.length <= 1
    ? names.join('')
    : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

/**
 * What the info icon beside the detect switch says. The examples are the
 * first built-in packs the registry lists (any registered pack when none is
 * built in), so the copy never names a pack the app does not have.
 */
function packsExplainer(packs: ExtractionDomain[], proposalReviewed: boolean): string {
  const builtin = packs.filter((p) => p.builtin);
  const examples = (builtin.length > 0 ? builtin : packs)
    .slice(0, EXAMPLE_PACKS)
    .map((p) => capitalizePack(p.name));
  const field = examples.length > 0 ? `that field, for example ${joinNames(examples)}.` : 'that field.';
  const detection = proposalReviewed
    ? 'With detection on, ChaosCypher reads a sample of your document and picks the packs that ' +
      'fit, and you review them before extraction starts.'
    : 'With detection on, ChaosCypher reads a sample of each page and picks the packs that fit; ' +
      'a whole-site import extracts with them straight away, without a review.';
  return (
    'Packs teach the extractor the vocabulary of a subject. Each pack adds the kinds of things ' +
    `and relationships to look for in ${field} ${detection}`
  );
}

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
  /**
   * Whether a detection proposal is reviewed before extraction (`allowAuto`):
   * true for a file or one page, which wait at the confirm step; false for a
   * whole-site import, whose pages extract with their detected packs at once.
   */
  proposalReviewed?: boolean;
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
  proposalReviewed = true,
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
    helper = proposalReviewed
      ? 'Detection proposes up to two packs; you review them before extraction starts.'
      : 'Detection picks up to two packs for each page; the pages extract with them straight away.';
  } else if (selected.length === 0) {
    helper = 'Floor only: the eight core types and the core relationships, no pack.';
  } else {
    helper = `Floor + ${packsLabel(selected)} · ~${(usage.tokens / 1000).toFixed(1)}k tokens (${usage.pct}%)`;
  }
  const overBudget = !auto && usage.pct > OVER_BUDGET_PCT;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
      {allowAuto && (
        <Box sx={{ display: 'flex', alignItems: 'center' }}>
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
            sx={{ mr: 0.5 }}
          />
          <Tooltip
            arrow
            placement="top"
            describeChild
            title={packsExplainer(packs, proposalReviewed)}
          >
            <Box
              component="span"
              tabIndex={0}
              aria-label="What are packs?"
              sx={{ display: 'inline-flex', color: 'text.secondary', cursor: 'help' }}
            >
              <InfoOutlinedIcon sx={{ fontSize: 16 }} />
            </Box>
          </Tooltip>
        </Box>
      )}
      {!auto && (
        <Autocomplete
          multiple
          size="small"
          disabled={disabled}
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
              placeholder={selected.length === 0 ? 'Floor only' : undefined}
              sx={accentSelectSx('domain')}
            />
          )}
        />
      )}
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
