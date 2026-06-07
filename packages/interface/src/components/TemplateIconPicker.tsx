// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TemplateIconPicker: Icon and color picker for template visual identity.
 *
 * Features:
 * - Auto mode (default): resolves icon/color from template name
 * - Curated icon grid organized by category
 * - Type-to-search for full MUI icon set (curated subset)
 * - Color palette with custom hex input
 *
 * NOTE: this component fundamentally relies on dynamic icon lookup by name
 * (`getMuiIcon(name)`), which React Compiler cannot statically analyse.
 * The `static-components` rule is disabled file-wide for that reason.
 */
/* eslint-disable react-hooks/static-components */

import React, { useState, useMemo } from 'react';
import {
  Box,
  Typography,
  TextField,
  InputAdornment,
  Chip,
  Tooltip,
  IconButton,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import CheckIcon from '@mui/icons-material/Check';
import ClearIcon from '@mui/icons-material/Clear';
import type { SvgIconComponent } from '@mui/icons-material';
import { ICON_REGISTRY, PICKER_CURATED_ICONS } from '../utils/iconRegistry';
import { getColorForTemplate } from '../utils/colorUtils';

const ALL_CURATED = Object.values(PICKER_CURATED_ICONS).flat();

const COLOR_PALETTE = [
  '#00E5FF', '#FF0080', '#00E676', '#BF00FF', '#FFB300',
  '#7C4DFF', '#FF003C', '#00BFA5', '#FF6D00', '#E040FB',
  '#448AFF', '#76FF03', '#18FFFF', '#FF4081', '#B388FF',
  '#00B8D4', '#69F0AE', '#FFAB00', '#D500F9', '#FF6E40',
];

function getMuiIcon(name: string): SvgIconComponent | null {
  return ICON_REGISTRY[name] || null;
}

interface TemplateIconPickerProps {
  icon: string | null | undefined;
  color: string | null | undefined;
  templateName?: string;
  templateId?: string;
  onIconChange: (icon: string | null) => void;
  onColorChange: (color: string | null) => void;
}

export const TemplateIconPicker: React.FC<TemplateIconPickerProps> = ({
  icon,
  color,
  templateName = '',
  templateId = '',
  onIconChange,
  onColorChange,
}) => {
  const [iconSearch, setIconSearch] = useState('');
  const [customHex, setCustomHex] = useState('');

  // Auto-resolved values (what "Auto" would pick)
  const autoColor = getColorForTemplate(templateId || templateName);

  // Determine display values
  const displayIcon = icon;
  const displayColor = color || autoColor;

  // Filter icons by search
  const filteredIcons = useMemo(() => {
    if (!iconSearch.trim()) return ALL_CURATED;
    const query = iconSearch.toLowerCase();
    return ALL_CURATED.filter(name => name.toLowerCase().includes(query));
  }, [iconSearch]);

  // Dynamic icon lookup by name string — React Compiler can't statically
  // determine the component type, but the lookup is intentional (icon name
  // comes from user-editable template config). The use sites of PreviewIcon
  // below also trigger the same rule and are intentionally suppressed.
  const PreviewIcon = useMemo(
    () => (displayIcon ? getMuiIcon(displayIcon) : null),
    [displayIcon],
  );

  return (
    <Box>
      {/* Preview */}
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 2,
          mb: 2.5,
          p: 2,
          bgcolor: 'action.hover',
          borderRadius: 2,
        }}
      >
        <Box
          sx={{
            width: 56,
            height: 56,
            borderRadius: '50%',
            bgcolor: displayColor,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            boxShadow: `0 2px 12px ${displayColor}66`,
          }}
        >
          {PreviewIcon && <PreviewIcon sx={{ color: 'white', fontSize: 28 }} />}
        </Box>
        <Box>
          <Typography variant="body2" sx={{
            fontWeight: 600
          }}>
            {templateName || 'Template'}
          </Typography>
          <Typography variant="caption" sx={{
            color: "text.secondary"
          }}>
            {icon ? `Icon: ${icon}` : 'No icon (plain circle)'}
            {' · '}
            {color ? `Color: ${color}` : 'Auto color'}
          </Typography>
        </Box>
      </Box>
      {/* Icon Section */}
      <Typography
        variant="overline"
        sx={{
          color: "text.secondary",
          display: 'block',
          mb: 1,
          fontWeight: 600
        }}>
        Icon
      </Typography>
      {/* Auto button */}
      <Box sx={{ display: 'flex', gap: 1, mb: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
        <Chip
          icon={<AutoAwesomeIcon />}
          label={icon === null || icon === undefined ? 'Auto (none)' : 'Set to Auto'}
          variant={!icon ? 'filled' : 'outlined'}
          color={!icon ? 'primary' : 'default'}
          onClick={() => onIconChange(null)}
          deleteIcon={!icon ? <CheckIcon /> : undefined}
          onDelete={!icon ? () => {} : undefined}
          size="small"
        />
        {icon && (
          <Chip
            icon={PreviewIcon ? <PreviewIcon sx={{ fontSize: 18 }} /> : undefined}
            label={icon}
            variant="filled"
            color="primary"
            onDelete={() => onIconChange(null)}
            deleteIcon={<ClearIcon />}
            size="small"
          />
        )}
      </Box>
      {/* Search */}
      <TextField
        size="small"
        fullWidth
        placeholder="Search icons..."
        value={iconSearch}
        onChange={(e) => setIconSearch(e.target.value)}
        sx={{ mb: 1.5 }}
        slotProps={{
          input: {
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon fontSize="small" />
              </InputAdornment>
            ),
          }
        }}
      />
      {/* Icon Grid */}
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: 'repeat(8, 1fr)',
          gap: 0.5,
          maxHeight: 200,
          overflowY: 'auto',
          mb: 2.5,
        }}
      >
        {filteredIcons.map((name) => {
          const Icon = getMuiIcon(name);
          if (!Icon) return null;
          const isSelected = icon === name;
          return (
            <Tooltip key={name} title={name} placement="top">
              <IconButton
                aria-label={name}
                size="small"
                onClick={() => onIconChange(name)}
                sx={{
                  width: 40,
                  height: 40,
                  borderRadius: 1.5,
                  border: isSelected ? 2 : 1,
                  borderColor: isSelected ? 'primary.main' : 'divider',
                  bgcolor: isSelected ? 'primary.main' : 'transparent',
                  color: isSelected ? 'white' : 'text.secondary',
                  '&:hover': {
                    bgcolor: isSelected ? 'primary.dark' : 'action.hover',
                  },
                }}
              >
                <Icon sx={{ fontSize: 20 }} />
              </IconButton>
            </Tooltip>
          );
        })}
      </Box>
      {/* Color Section */}
      <Typography
        variant="overline"
        sx={{
          color: "text.secondary",
          display: 'block',
          mb: 1,
          fontWeight: 600
        }}>
        Color
      </Typography>
      <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
        {/* Auto button */}
        <Chip
          icon={<AutoAwesomeIcon />}
          label="Auto"
          variant={!color ? 'filled' : 'outlined'}
          color={!color ? 'primary' : 'default'}
          onClick={() => onColorChange(null)}
          deleteIcon={!color ? <CheckIcon /> : undefined}
          onDelete={!color ? () => {} : undefined}
          size="small"
        />

        <Box sx={{ width: 1, height: 24, bgcolor: 'divider', mx: 0.5 }} />

        {/* Color palette */}
        {COLOR_PALETTE.map((c) => (
          <Tooltip key={c} title={c} placement="top">
            <Box
              onClick={() => onColorChange(c)}
              sx={{
                width: 28,
                height: 28,
                borderRadius: '50%',
                bgcolor: c,
                cursor: 'pointer',
                border: 2,
                borderColor: color === c ? 'primary.main' : 'transparent',
                opacity: !color ? 0.5 : 1,
                transition: 'all 0.15s',
                '&:hover': { opacity: 1, transform: 'scale(1.15)' },
              }}
            />
          </Tooltip>
        ))}

        {/* Custom hex input */}
        <TextField
          size="small"
          placeholder="#hex"
          value={customHex}
          onChange={(e) => setCustomHex(e.target.value)}
          onBlur={() => {
            if (/^#[0-9a-fA-F]{6}$/.test(customHex)) {
              onColorChange(customHex);
            }
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && /^#[0-9a-fA-F]{6}$/.test(customHex)) {
              onColorChange(customHex);
            }
          }}
          sx={{ width: 90 }}
          slotProps={{
            input: { sx: { fontSize: 12, fontFamily: 'monospace' } }
          }}
        />
      </Box>
    </Box>
  );
};
