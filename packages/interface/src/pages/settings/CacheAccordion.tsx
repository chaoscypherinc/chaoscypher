// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * CacheAccordion — the LLM response and embedding caches on the General
 * Settings tab: one card per cache with its enable switch, size against the
 * cap, hit rate, time saved, the split by call site, and a Clear action.
 *
 * Changes go through `PATCH /api/v1/cache` (persisted to settings.yaml), not
 * the page-wide Save, so a toggle applies immediately.
 */

import { useState } from 'react';
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  LinearProgress,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import CachedIcon from '@mui/icons-material/Cached';
import ConfirmDialog from '../../components/ConfirmDialog';
import { accordionSummarySx, accentAccordionSx } from '../../theme/settings';
import { ACCENT_COLORS } from '../../theme/accentStyles';
import { surfaceSx } from '../../theme/cardStyles';
import {
  ghostErrorAlertSx,
  ghostSwitchSx,
  ghostTableHeadCellSx,
  ghostTableRowSx,
} from '../../theme/ghostStyles';
import { getApiErrorMessage } from '../../utils/errors';
import { formatDuration, formatFileSize } from '../../utils/formatters';
import type { CacheStats, CacheUpdate } from '../../services/api/cache';
import { useCache } from './hooks/useCache';

const ACCENT = 'info';

const CACHE_LABELS: Record<string, string> = {
  completions: 'Completions',
  embeddings: 'Embeddings',
};

function cacheLabel(name: string): string {
  return CACHE_LABELS[name] ?? name;
}

function formatSaved(ms: number): string {
  return formatDuration(ms / 1000, { withSeconds: true });
}

interface CacheCardProps {
  cache: CacheStats;
  busy: boolean;
  onToggle: (enabled: boolean) => void;
  onClear: () => void;
}

function CacheCard({ cache, busy, onToggle, onClear }: CacheCardProps) {
  const label = cacheLabel(cache.name);
  const used = cache.max_bytes > 0 ? Math.min(100, (cache.bytes / cache.max_bytes) * 100) : 0;
  const sites = Object.entries(cache.by_call_site);

  return (
    <Box sx={{ ...surfaceSx, p: 2 }} data-testid={`cache-card-${cache.name}`}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5 }}>
        <Typography variant="subtitle2" sx={{ fontWeight: 'medium' }}>
          {label}
        </Typography>
        <Switch
          size="small"
          checked={cache.enabled}
          disabled={busy}
          onChange={(e) => onToggle(e.target.checked)}
          sx={ghostSwitchSx}
          slotProps={{ input: { 'aria-label': `${label} cache enabled` } }}
        />
        <Button
          size="small"
          variant="outlined"
          color="warning"
          disabled={busy || cache.entries === 0}
          onClick={onClear}
          sx={{ ml: 'auto', textTransform: 'none' }}
        >
          Clear
        </Button>
      </Box>

      <Box sx={{ display: 'flex', gap: 3, mb: 1.5, flexWrap: 'wrap' }}>
        <Stat label="Entries" value={cache.entries.toLocaleString()} />
        <Tooltip title={`${cache.hits.toLocaleString()} hits · ${cache.misses.toLocaleString()} misses`}>
          <Box>
            <Stat label="Hit rate" value={`${Math.round(cache.hit_rate * 100)}%`} />
          </Box>
        </Tooltip>
        <Stat label="Time saved" value={formatSaved(cache.latency_saved_ms)} />
      </Box>

      <Box sx={{ mb: sites.length ? 1.5 : 0 }}>
        <LinearProgress
          variant="determinate"
          value={used}
          aria-label={`${label} cache size`}
          sx={{ height: 4, borderRadius: 2 }}
        />
        <Typography variant="caption" sx={{ color: 'text.secondary' }}>
          {formatFileSize(cache.bytes)} / {formatFileSize(cache.max_bytes)}
        </Typography>
      </Box>

      {sites.length > 0 && (
        <Table size="small" aria-label={`${label} by call site`}>
          <TableHead>
            <TableRow>
              {['Site', 'Hits', 'Misses', 'Bypasses', 'Saved'].map((h, i) => (
                <TableCell key={h} align={i === 0 ? 'left' : 'right'} sx={ghostTableHeadCellSx}>
                  {h}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {sites.map(([site, s]) => (
              <TableRow key={site} sx={ghostTableRowSx}>
                <TableCell>{site}</TableCell>
                <TableCell align="right">{s.hits.toLocaleString()}</TableCell>
                <TableCell align="right">{s.misses.toLocaleString()}</TableCell>
                <TableCell align="right">{s.bypasses.toLocaleString()}</TableCell>
                <TableCell align="right">{formatSaved(s.latency_saved_ms)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Box>
  );
}

interface StatProps {
  label: string;
  value: string;
}

function Stat({ label, value }: StatProps) {
  return (
    <Box>
      <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block' }}>
        {label}
      </Typography>
      <Typography variant="body2" sx={{ fontFamily: 'ui-monospace, monospace' }}>
        {value}
      </Typography>
    </Box>
  );
}

export default function CacheAccordion() {
  const { overview, loading, error, pending, update, clear } = useCache();
  const [confirmName, setConfirmName] = useState<string | null>(null);

  // Failures land in the hook's `error` (shown below); swallow the rejection here.
  const handleToggle = (name: string, enabled: boolean) => {
    // reason: `name` is one of the overview's cache names, the keys of the update body.
    update({ [name]: { enabled } } as CacheUpdate).catch(() => {});
  };

  const handleConfirmClear = () => {
    const name = confirmName;
    setConfirmName(null);
    if (name) clear(name).catch(() => {});
  };

  return (
    <Accordion sx={accentAccordionSx(ACCENT)}>
      <AccordionSummary
        expandIcon={<ExpandMoreIcon sx={{ color: ACCENT_COLORS[ACCENT] }} />}
        sx={accordionSummarySx}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flex: 1, mr: 2 }}>
          <CachedIcon sx={{ fontSize: 18, color: ACCENT_COLORS[ACCENT] }} />
          <Box>
            <Typography variant="subtitle2" sx={{ fontWeight: 'medium' }}>
              Cache
            </Typography>
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              Pinned answers and embeddings served again without the model
            </Typography>
          </Box>
        </Box>
      </AccordionSummary>
      <AccordionDetails>
        {error && (
          <Alert severity="error" sx={{ mb: 2, ...ghostErrorAlertSx }}>
            {getApiErrorMessage(error) || 'Failed to load the cache'}
          </Alert>
        )}
        {loading && <LinearProgress sx={{ mb: 2 }} />}
        {overview && (
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
              gap: 2,
            }}
          >
            {overview.caches.map((cache) => (
              <CacheCard
                key={cache.name}
                cache={cache}
                busy={pending}
                onToggle={(enabled) => handleToggle(cache.name, enabled)}
                onClear={() => setConfirmName(cache.name)}
              />
            ))}
          </Box>
        )}
      </AccordionDetails>
      <ConfirmDialog
        open={confirmName !== null}
        title={`Clear the ${cacheLabel(confirmName ?? '').toLowerCase()} cache?`}
        message="Every stored entry is deleted; the next calls go to the model again."
        confirmLabel="Clear"
        confirmColor="warning"
        onConfirm={handleConfirmClear}
        onCancel={() => setConfirmName(null)}
      />
    </Accordion>
  );
}
