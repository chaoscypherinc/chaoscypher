// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Shared MUI icon utilities.
 *
 * Resolves MUI icon components by name string, with fallback defaults.
 * Used by templates, domains, and any component that renders dynamic icons.
 *
 * Icon set is curated via `iconRegistry.ts` — names not registered there
 * resolve to the default fallback (ArticleOutlined). An ESLint rule bans
 * the barrel import (`from '@mui/icons-material'`) to prevent the whole
 * 4 MB icon catalog from being pulled into the bundle.
 */
import type { SvgIconComponent } from '@mui/icons-material';
import { ICON_REGISTRY } from './iconRegistry';

const DEFAULT_DOMAIN_ICON = 'Article';

/**
 * Get a MUI icon component by name.
 *
 * Always resolves to the Outlined variant (e.g. `Person` → `PersonOutlined`)
 * so template icons render with a lighter, stroked look across the app. Names
 * that already end in `Outlined` are passed through unchanged. Falls back to
 * `ArticleOutlined` if the name is null, undefined, or not in the registry.
 *
 * Note: the graph canvas uses a separate SVG-path-based sprite renderer in
 * `GraphCanvasPage/utils/iconSprites.ts` and is unaffected by this resolver.
 */
export function getMuiIcon(name: string | null | undefined): SvgIconComponent {
  const toOutlined = (n: string) => (n.endsWith('Outlined') ? n : `${n}Outlined`);
  const fallback = ICON_REGISTRY[toOutlined(DEFAULT_DOMAIN_ICON)];
  if (!name) return fallback;
  return ICON_REGISTRY[toOutlined(name)] || fallback;
}
