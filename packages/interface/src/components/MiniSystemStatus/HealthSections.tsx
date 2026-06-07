// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * HealthSections: Grouped health check rows for the status dropdown.
 *
 * Renders AI, Processing, and Storage sections, each condensed to a single
 * row when all checks are healthy and expanded with error/warning styling
 * when problems are detected.
 */

import {
  Box,
  IconButton,
  ListItemIcon,
  ListItemText,
  MenuItem,
  Tooltip,
  Typography,
 alpha } from '@mui/material';
import DocsIcon from '@mui/icons-material/OpenInNew';
import DotIcon from '@mui/icons-material/FiberManualRecord';
import {
  Bot,
  Cpu,
  HardDrive,
  Settings as LucideSettings,
} from 'lucide-react';

import { StatusColors } from '../../theme/colors';
import type { HealthCheckResponse } from '../../types/health';

/** Section group configuration for the health dropdown. */
interface HealthSection {
  label: string;
  icon: React.ReactNode;
  color: string;
  keys: string[];
}

/** Health section groupings with icons and colors. */
const HEALTH_SECTIONS: HealthSection[] = [
  {
    label: 'AI',
    icon: <Bot size={15} strokeWidth={1.5} />,
    color: alpha('#fff', 0.25),
    keys: ['ollama', 'chat_model', 'extraction_model', 'vision_model', 'embeddings', 'provider'],
  },
  {
    label: 'Processing',
    icon: <Cpu size={15} strokeWidth={1.5} />,
    color: alpha('#fff', 0.25),
    keys: ['queue', 'llm_worker', 'ops_worker', 'error_rate'],
  },
  {
    label: 'Storage',
    icon: <HardDrive size={15} strokeWidth={1.5} />,
    color: alpha('#fff', 0.25),
    keys: ['search_index', 'graph', 'database', 'disk_space'],
  },
];

/** Human-readable labels for health check keys. */
const HEALTH_CHECK_LABELS: Record<string, string> = {
  ollama: 'Ollama',
  chat_model: 'Chat Model',
  extraction_model: 'Extraction',
  vision_model: 'Vision',
  provider: 'Provider',
  embeddings: 'Embeddings',
  queue: 'Queue',
  llm_worker: 'LLM Worker',
  ops_worker: 'Ops Worker',
  search_index: 'Search Index',
  graph: 'Graph',
  error_rate: 'Error Rate',
  database: 'Database',
  disk_space: 'Disk Space',
};

/** Navigation targets for health check items. */
const HEALTH_NAVIGATE: Record<string, string> = {
  ollama: '/settings?tab=models',
  chat_model: '/settings?tab=models',
  extraction_model: '/settings?tab=models',
  vision_model: '/settings?tab=models',
  provider: '/settings?tab=models',
  embeddings: '/settings?tab=search',
  queue: '/queues',
  llm_worker: '/queues',
  ops_worker: '/queues',
  search_index: '/settings?tab=search',
  graph: '/graph',
  error_rate: '/queues',
  database: '/settings?tab=maintenance',
  disk_space: '/settings',
};

/** Map a status level to a dot color. */
function statusDotColor(status: 'ok' | 'warning' | 'error'): string {
  if (status === 'ok') return StatusColors.healthy;
  if (status === 'warning') return StatusColors.warning;
  return StatusColors.failed;
}

/** Build a short summary string for each health section. */
function buildSectionSummary(
  sectionLabel: string,
  checks: NonNullable<HealthCheckResponse['checks']>,
  keys: string[],
): string {
  const get = (key: string) => checks[key]?.message || '';
  const detail = (key: string, field: string) => {
    const d = checks[key]?.details;
    return d ? (d[field] as string | number | undefined) : undefined;
  };

  switch (sectionLabel) {
    case 'AI': {
      const model =
        String(detail('chat_model', 'model') || '') || get('chat_model').replace(' installed', '');
      const connected = checks['ollama']
        ? 'Connected'
        : checks['provider']
          ? get('provider')
          : '';
      return connected && model ? `${connected} · ${model}` : connected || model || 'Configured';
    }
    case 'Processing': {
      const llm = get('llm_worker').replace('Running ', '').replace('(', '').replace(')', '');
      const ops = get('ops_worker').replace('Running ', '').replace('(', '').replace(')', '');
      return `LLM ${llm} · Ops ${ops}`;
    }
    case 'Storage': {
      const freeHuman = detail('disk_space', 'free_human');
      const dbStatus = checks['database']?.status === 'ok' ? 'DB healthy' : '';
      const parts: string[] = [];
      if (freeHuman) parts.push(`${freeHuman} free`);
      if (dbStatus) parts.push(dbStatus);
      return parts.join(' · ') || 'OK';
    }
    default:
      return keys.map(k => get(k)).filter(Boolean).join(' · ');
  }
}

interface HealthSectionsProps {
  /** Full health check response from the API. */
  health: HealthCheckResponse;
  /** Callback to navigate to a route and close the menu. */
  onNavigate: (path: string) => void;
}

/**
 * Render grouped health check rows inside the status dropdown menu.
 *
 * Each section is condensed to a single row when all checks are OK, and
 * highlighted with error/warning styling when problems exist. Any backend
 * health checks not mapped to a defined section appear under "Other".
 */
export function HealthSections({ health, onNavigate }: HealthSectionsProps) {
  // Collect all keys mapped by defined sections
  const mappedKeys = new Set(HEALTH_SECTIONS.flatMap(s => s.keys));

  // checks is absent for unauthenticated probes; the component is only
  // rendered from the authenticated dashboard, so this guard is defensive.
  const checks = health.checks ?? {};

  // Find any unmapped keys from the backend response
  const unmappedKeys = Object.keys(checks).filter(k => !mappedKeys.has(k));

  // Build the full list of sections including a catch-all for unmapped probes
  const allSections: HealthSection[] = unmappedKeys.length > 0
    ? [
        ...HEALTH_SECTIONS,
        {
          label: 'Other',
          icon: <LucideSettings size={15} strokeWidth={1.5} />,
          color: alpha('#fff', 0.25),
          keys: unmappedKeys,
        },
      ]
    : HEALTH_SECTIONS;

  return (
    <>
      {/* Section header */}
      <Box sx={{ px: 2, py: 1, borderBottom: '1px solid rgba(255, 255, 255, 0.06)' }}>
        <Typography
          sx={{
            fontSize: '0.7rem',
            color: alpha('#fff', 0.19),
            letterSpacing: '1.5px',
            textTransform: 'uppercase',
          }}
        >
          Status
        </Typography>
      </Box>

      {allSections.map(section => {
        const visibleChecks = section.keys.filter(key => checks[key]);
        if (visibleChecks.length === 0) return null;

        // Check if all items in this section are ok - if so, condense to one row
        const allOk = visibleChecks.every(key => checks[key].status === 'ok');
        const problemChecks = visibleChecks.filter(key => checks[key].status !== 'ok');

        // Build summary for the section
        const sectionStatus = allOk
          ? 'ok'
          : problemChecks.some(k => checks[k].status === 'error')
            ? 'error'
            : 'warning';
        const dotColor = statusDotColor(sectionStatus as 'ok' | 'warning' | 'error');
        const isError = sectionStatus === 'error';
        const isWarning = sectionStatus === 'warning';
        const hasProblem = isError || isWarning;

        // Build secondary text: short summary per section. When multiple
        // problems exist in the same section (e.g. Ollama down breaks both
        // chat and embeddings), surface every problem message — collapsing
        // to the first one silently hides the rest.
        let secondary: string;
        if (hasProblem) {
          secondary = problemChecks.map(k => checks[k].message).join('\n');
        } else {
          secondary = buildSectionSummary(section.label, checks, visibleChecks);
        }
        const multiProblem = hasProblem && problemChecks.length > 1;

        // Tooltip: full details for all checks
        const tooltipText = hasProblem
          ? problemChecks
              .map(k => {
                const c = checks[k];
                return (c.details?.tooltip as string) || c.message;
              })
              .join('\n')
          : visibleChecks
              .map(key => {
                const c = checks[key];
                const label = HEALTH_CHECK_LABELS[key] || key;
                return `${label}: ${c.message}`;
              })
              .join('\n');

        const navPath = hasProblem
          ? HEALTH_NAVIGATE[problemChecks[0]] || '/'
          : HEALTH_NAVIGATE[visibleChecks[0]] || '/';

        return (
          <Tooltip
            key={section.label}
            title={tooltipText}
            placement="left"
            slotProps={{
              tooltip: {
                sx: { whiteSpace: 'pre-line', fontSize: '0.7rem', maxWidth: 280 },
              },
            }}
          >
            <MenuItem
              onClick={() => onNavigate(navPath)}
              sx={{
                py: 1,
                pl: 2,
                pr: 3.5,
                minHeight: 'auto',
                transition: 'all 0.15s ease-in-out',
                ...(isError && {
                  bgcolor: 'rgba(244, 67, 54, 0.06)',
                  borderLeft: `3px solid ${StatusColors.failed}`,
                  '&:hover': { bgcolor: 'rgba(244, 67, 54, 0.12)' },
                }),
                ...(isWarning && {
                  bgcolor: 'rgba(255, 152, 0, 0.06)',
                  borderLeft: `3px solid ${StatusColors.warning}`,
                  '&:hover': { bgcolor: 'rgba(255, 152, 0, 0.12)' },
                }),
              }}
            >
              <DotIcon sx={{ fontSize: 10, color: dotColor, mr: 1.5 }} />
              <ListItemText
                primary={section.label}
                secondary={secondary}
                slotProps={{
                  primary: {
                    sx: { fontSize: '0.8rem', fontWeight: hasProblem ? 600 : 500 },
                  },
                  secondary: {
                    noWrap: !multiProblem,
                    sx: {
                      fontSize: '0.7rem',
                      ...(multiProblem && { whiteSpace: 'pre-line' }),
                    },
                  },
                }}
              />
              <ListItemIcon sx={{ minWidth: 20, color: section.color, ml: 'auto', justifyContent: 'flex-end' }}>
                {section.icon}
              </ListItemIcon>
              {isError && (
                <Tooltip title="Troubleshooting docs">
                  <IconButton
                    aria-label="Troubleshooting docs"
                    size="small"
                    onClick={e => {
                      e.stopPropagation();
                      window.open(
                        'https://chaoscypher.com/docs/user-guide/troubleshooting/',
                        '_blank',
                        'noopener,noreferrer',
                      );
                    }}
                    sx={{ ml: 0.5, p: 0.5, opacity: 0.6, '&:hover': { opacity: 1 } }}
                  >
                    <DocsIcon sx={{ fontSize: 14 }} />
                  </IconButton>
                </Tooltip>
              )}
            </MenuItem>
          </Tooltip>
        );
      })}
    </>
  );
}
