// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * @module PacksChip
 * The packs a source was extracted with, as one chip: an icon per pack and
 * the `+`-joined label (`Historical + Literary`); "Floor only" when the
 * source used no pack. Shared by the source page header and the source list.
 */

import { createElement, type ReactNode } from 'react';
import { Box, Chip } from '@mui/material';
import { getMuiIcon } from '../../utils/icons';
import {
  FLOOR_DOMAIN,
  packIconOf,
  packsLabel,
  sourcePacks,
  type SourcePacksFields,
} from '../../utils/packs';

interface PackIconsProps {
  source: SourcePacksFields;
  packs: string[];
  color: string;
  fontSize?: number;
}

/** One icon per pack, side by side (the floor's document icon for none). */
export function PackIcons({ source, packs, color, fontSize = 16 }: PackIconsProps) {
  const names = packs.length === 0 ? [null] : packs;
  return (
    <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.25 }}>
      {names.map((pack) => (
        <Box
          component="span"
          key={pack ?? FLOOR_DOMAIN}
          data-testid={`pack-icon-${pack ?? FLOOR_DOMAIN}`}
          sx={{ display: 'inline-flex' }}
        >
          {createElement(getMuiIcon(pack ? packIconOf(source, pack) : null), {
            sx: { fontSize, color: `${color} !important` },
          })}
        </Box>
      ))}
    </Box>
  );
}

interface PacksChipProps {
  source: SourcePacksFields;
  color: string;
  /** Appended to the label (e.g. ` (Custom)` for a user's choice). */
  suffix?: ReactNode;
}

/** The source's packs as one outlined chip: an icon per pack and the `+` label. */
export function PacksChip({ source, color, suffix }: PacksChipProps) {
  const packs = sourcePacks(source);
  if (packs === null) return null;
  return (
    <Chip
      icon={<PackIcons source={source} packs={packs} color={color} />}
      label={
        <>
          {packsLabel(packs)}
          {suffix}
        </>
      }
      size="small"
      variant="outlined"
      sx={{
        height: 24,
        fontSize: '0.75rem',
        fontWeight: 500,
        bgcolor: 'transparent',
        borderColor: color,
        color,
        '& .MuiChip-label': { px: 1.5 },
        '& .MuiChip-icon': { ml: 0.75 },
      }}
    />
  );
}
