// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/* eslint-disable react-hooks/static-components */
// getMuiIcon returns stable module-level MUI icon components (module exports
// from @mui/icons-material), so the dynamic-component warning is a false
// positive. Matches the convention in TemplateIconPicker.tsx / TemplatesPage.tsx.

import { Box } from '@mui/material';
import { getMuiIcon } from '../utils/icons';
import { getColorForTemplate } from '../utils/colorUtils';
import {
  DEFAULT_NODE_ICON,
  DEFAULT_EDGE_ICON,
} from '../utils/iconSprites';

/**
 * Minimum shape TemplateIcon needs. Accepts both full `Template` objects and
 * narrower shapes like `SourceTemplate` — the component only reads `id`,
 * `color`, and `icon`.
 */
interface TemplateIconData {
  id: string;
  color?: string | null;
  icon?: string | null;
}

interface TemplateIconProps {
  /** Template object; icon/color take precedence when present. */
  template?: TemplateIconData | null;
  /** Fallback template id used to compute the color when `template` is null. */
  fallbackTemplateId?: string;
  /** Determines the fallback icon when `template` has no icon set. */
  variant?: 'node' | 'edge';
  /** Icon fontSize in px (default: 24). */
  size?: number;
  /** Outer container size in px (default: size + 16). */
  containerSize?: number;
  /**
   * Visual treatment. Default (`false`) renders the icon in the template
   * color on a transparent container. When `true`, renders a filled circle
   * with the template color as the background and a white icon inside —
   * used by TemplateSelectionModal and the ExtractionTab template rows.
   */
  filled?: boolean;
}

/**
 * Template-aware icon: renders the template's configured icon (or a default
 * for the variant) in its configured color (or an auto-generated color
 * derived from the template id). When `filled` is true, renders a filled
 * circle with the color as background and a white icon inside.
 */
export default function TemplateIcon({
  template,
  fallbackTemplateId,
  variant = 'node',
  size = 24,
  containerSize,
  filled = false,
}: TemplateIconProps) {
  const resolvedId = template?.id ?? fallbackTemplateId;
  const color = template?.color || getColorForTemplate(resolvedId);
  const fallbackIcon = variant === 'edge' ? DEFAULT_EDGE_ICON : DEFAULT_NODE_ICON;
  const Icon = getMuiIcon(template?.icon || fallbackIcon);
  const box = containerSize ?? size + 16;

  return (
    <Box
      sx={{
        width: box,
        height: box,
        borderRadius: filled ? '50%' : undefined,
        bgcolor: filled ? color : undefined,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
      }}
    >
      {Icon && <Icon sx={{ color: filled ? 'common.white' : color, fontSize: size }} />}
    </Box>
  );
}
