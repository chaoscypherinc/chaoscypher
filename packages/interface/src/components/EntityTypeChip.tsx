// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * @module EntityTypeChip
 * An entity's type as a chip: coloured by its core type (the per-source
 * template the commit creates for each core type), with the pack's subtype in
 * the label when the entity carries one (`Person · Character`). Spec
 * 2026-10-07 § 6: a node's `entity_type` is one of the eight core types and
 * anything more specific is `properties.entity_subtype`.
 */

import { createElement } from 'react';
import { Chip } from '@mui/material';
import { alpha } from '@mui/material/styles';
import { getMuiIcon } from '../utils/icons';
import { typeLabel } from '../utils/entityTypes';
import { DEFAULT_NODE_ICON } from '../utils/iconSprites';

interface EntityTypeChipProps {
  coreType: string | null | undefined;
  subtype?: string;
  /** The core type's colour (its template's). */
  color: string;
  /** The subtype's own icon when the pack gives one, else the core type's. */
  icon?: string | null;
}

export function EntityTypeChip({ coreType, subtype, color, icon }: EntityTypeChipProps) {
  return (
    <Chip
      icon={createElement(getMuiIcon(icon || DEFAULT_NODE_ICON), {
        sx: { color: `${color} !important`, fontSize: 14 },
      })}
      label={typeLabel(coreType, subtype)}
      size="small"
      variant="outlined"
      sx={{
        height: 20,
        fontSize: '0.65rem',
        bgcolor: 'transparent',
        borderColor: alpha(color, 0.5),
        color,
        '& .MuiChip-icon': { ml: '4px', mr: '-2px' },
      }}
    />
  );
}
