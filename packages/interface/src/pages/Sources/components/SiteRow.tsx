// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Site Table Row Component
 *
 * An imported docs site as one row of the sources table: its title and
 * address, "43 of 71 pages" with a progress bar (computed from its pages by
 * the backend), its run status, and a menu with "Check for updates" and
 * "Delete site" (both disabled, with the reason, while a run is live:
 * `run_in_progress`, so a run a killed worker left behind does not lock them).
 * The pages the site leaves out (unticked at import, or deleted from it) are
 * counted ("2 pages left out": focusable, a few named in its tooltip, then
 * "and N more", `web.site_pages_named_max`); a check keeps them out, and
 * "Include the left-out pages" asks first (deleted pages come back too),
 * then checks with every page brought back.
 * Expanding it lists its pages as ordinary source rows (`renderPage`); they
 * load, and poll while the site is busy, only while it is expanded.
 */

import { useState, type ReactNode } from 'react';
import {
  Box,
  Chip,
  IconButton,
  LinearProgress,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  TableCell,
  TableRow,
  Tooltip,
  Typography,
} from '@mui/material';
import RestoreIcon from '@mui/icons-material/Restore';
import ChevronRightIcon from '@mui/icons-material/ChevronRight';
import DeleteIcon from '@mui/icons-material/Delete';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import LanguageIcon from '@mui/icons-material/Language';
import MoreVertIcon from '@mui/icons-material/MoreVert';
import RefreshIcon from '@mui/icons-material/Refresh';
import type { Site, UnifiedSource } from '../../../types';
import {
  siteIsBusy,
  siteRunInProgress,
  useDeleteSite,
  useRefreshSite,
  useSitePages,
} from '../../../services/api/useSites';
import { useNotification } from '../../../contexts/useNotification';
import { getApiErrorMessage } from '../../../utils/errors';
import { DeleteSiteDialog } from '../dialogs/DeleteSiteDialog';
import ConfirmDialog from '../../../components/ConfirmDialog';
import { useAppConfig } from '../../../contexts/useAppConfig';

const STATUS_LABELS: Record<Site['status'], string> = {
  queued: 'Queued',
  importing: 'Importing',
  refreshing: 'Checking for updates',
  imported: 'Imported',
  failed: 'Failed',
};

const STATUS_COLORS: Record<Site['status'], 'default' | 'info' | 'success' | 'error'> = {
  queued: 'default',
  importing: 'info',
  refreshing: 'info',
  imported: 'success',
  failed: 'error',
};

/** Why "Check for updates" and "Delete site" wait while a run is live. */
const RUN_BLOCKED_REASONS: Partial<Record<Site['status'], string>> = {
  queued: 'Waiting to start',
  importing: 'The import is still running',
  refreshing: 'A check is already running',
};

interface SiteRowProps {
  /** The site to render. */
  site: Site;
  /** Poll interval (ms) for its pages while the site is busy and expanded. */
  pollInterval: number;
  /** Render one of the site's pages (the table's own source row). */
  renderPage: (page: UnifiedSource) => ReactNode;
}

/** "1 page" / "2 pages". */
function pagesWord(count: number): string {
  return count === 1 ? '1 page' : `${count} pages`;
}

/** A few addresses, then "and N more" (the backend's `name_pages`, `web.site_pages_named_max`). */
function namePages(urls: string[], limit: number): string {
  const shown = urls.slice(0, limit).join(', ');
  const rest = urls.length - limit;
  return rest > 0 ? `${shown} and ${rest} more` : shown;
}

/** The site's progress cell: "43 of 71 pages", a bar, and what needs attention. */
function SiteProgressCell({ site }: { site: Site }) {
  const { total, done, failed, awaiting_confirmation: awaiting, gone } = site.progress;
  const percent = total > 0 ? Math.round((done / total) * 100) : 0;
  const finding = total === 0 && siteRunInProgress(site);
  const notes = [
    failed > 0 ? `${failed} failed` : null,
    awaiting > 0 ? `${awaiting} awaiting confirmation` : null,
    gone > 0 ? `${gone} gone from the site` : null,
  ].filter((note): note is string => note !== null);
  const leftOut = site.excluded_pages;
  const { web_site_pages_named_max: namedMax } = useAppConfig();

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, minWidth: 160 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <Chip label={STATUS_LABELS[site.status]} color={STATUS_COLORS[site.status]} size="small" variant="outlined" />
        <Typography variant="body2">{finding ? 'Finding pages…' : `${done} of ${total} pages`}</Typography>
      </Box>
      <LinearProgress
        variant={finding ? 'indeterminate' : 'determinate'}
        value={percent}
        aria-label={`${site.title} progress`}
        sx={{ height: 4, borderRadius: 2 }}
      />
      {site.status === 'failed' && site.error_message && (
        <Typography variant="caption" sx={{ color: 'error.main' }}>
          {site.error_message}
        </Typography>
      )}
      {notes.length > 0 && (
        <Typography variant="caption" sx={{ color: 'text.secondary' }}>
          {notes.join(' · ')}
        </Typography>
      )}
      {leftOut.length > 0 && (
        <Tooltip title={namePages(leftOut, namedMax)} describeChild>
          <Typography
            variant="caption"
            tabIndex={0}
            sx={{ color: 'text.secondary', width: 'fit-content' }}
          >
            {`${pagesWord(leftOut.length)} left out`}
          </Typography>
        </Tooltip>
      )}
    </Box>
  );
}

/** One imported site, expandable to its pages. */
export function SiteRow({ site, pollInterval, renderPage }: SiteRowProps) {
  const [expanded, setExpanded] = useState(false);
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const { notify } = useNotification();
  const refreshSite = useRefreshSite();
  const deleteSite = useDeleteSite();
  const pages = useSitePages(site.id, {
    enabled: expanded,
    refetchInterval: expanded && siteIsBusy(site) ? pollInterval : false,
  });

  const blockedReason = siteRunInProgress(site)
    ? (RUN_BLOCKED_REASONS[site.status] ?? 'A run is in progress')
    : undefined;

  /** Check for updates; `exclude` replaces the pages the site leaves out (none: they stay). */
  const handleRefresh = (exclude?: string[]) => {
    setMenuAnchor(null);
    refreshSite.mutate(
      { siteId: site.id, exclude },
      { onError: (err) => notify('Check for updates failed: ' + getApiErrorMessage(err), 'error') },
    );
  };
  const leftOut = site.excluded_pages.length;
  const [includeOpen, setIncludeOpen] = useState(false);

  const handleDelete = (deletePages: boolean) => {
    setDeleteOpen(false);
    deleteSite.mutate(
      { siteId: site.id, deletePages },
      { onError: (err) => notify('Delete failed: ' + getApiErrorMessage(err), 'error') },
    );
  };

  return (
    <>
      <TableRow sx={{ cursor: 'pointer' }} onClick={() => setExpanded((open) => !open)}>
        <TableCell padding="checkbox">
          <IconButton
            size="small"
            aria-label={expanded ? 'Hide pages' : 'Show pages'}
            aria-expanded={expanded}
            onClick={(e) => {
              e.stopPropagation();
              setExpanded((open) => !open);
            }}
          >
            {expanded ? <ExpandMoreIcon /> : <ChevronRightIcon />}
          </IconButton>
        </TableCell>
        <TableCell>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <LanguageIcon sx={{ fontSize: 18, color: 'text.secondary' }} />
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="body2">{site.title}</Typography>
              <Typography
                variant="caption"
                component="div"
                sx={{ color: 'text.secondary', overflow: 'hidden', textOverflow: 'ellipsis' }}
              >
                {site.url}
              </Typography>
            </Box>
          </Box>
        </TableCell>
        <TableCell>
          <SiteProgressCell site={site} />
        </TableCell>
        <TableCell align="right" onClick={(e) => e.stopPropagation()}>
          <Tooltip title="Actions">
            <IconButton
              aria-label="Site actions"
              size="small"
              onClick={(e) => setMenuAnchor(e.currentTarget)}
            >
              <MoreVertIcon />
            </IconButton>
          </Tooltip>
        </TableCell>
      </TableRow>
      {expanded && pages.isLoading && (
        <TableRow>
          <TableCell />
          <TableCell colSpan={3}>
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>Loading pages…</Typography>
          </TableCell>
        </TableRow>
      )}
      {expanded && pages.data?.length === 0 && (
        <TableRow>
          <TableCell />
          <TableCell colSpan={3}>
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>No pages yet.</Typography>
          </TableCell>
        </TableRow>
      )}
      {expanded && pages.data?.map((page) => renderPage(page))}
      <Menu anchorEl={menuAnchor} open={menuAnchor !== null} onClose={() => setMenuAnchor(null)}>
        <MenuItem onClick={() => handleRefresh()} disabled={blockedReason !== undefined}>
          <ListItemIcon>
            <RefreshIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText primary="Check for updates" secondary={blockedReason} />
        </MenuItem>
        {leftOut > 0 && (
          <MenuItem
            onClick={() => {
              setMenuAnchor(null);
              setIncludeOpen(true);
            }}
            disabled={blockedReason !== undefined}
          >
            <ListItemIcon>
              <RestoreIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText
              primary={leftOut === 1 ? 'Include the left-out page' : 'Include the left-out pages'}
              secondary={
                blockedReason ?? `Checks for updates with ${pagesWord(leftOut)} brought back`
              }
            />
          </MenuItem>
        )}
        <MenuItem
          onClick={() => {
            setMenuAnchor(null);
            setDeleteOpen(true);
          }}
          disabled={blockedReason !== undefined}
        >
          <ListItemIcon>
            <DeleteIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText primary="Delete site" secondary={blockedReason} />
        </MenuItem>
      </Menu>
      <ConfirmDialog
        open={includeOpen}
        title={`Bring back ${pagesWord(leftOut)}?`}
        message={
          `This checks ${site.title} for updates with every page included: the ` +
          `${pagesWord(leftOut)} left out are imported again, including any you deleted from the site.`
        }
        confirmLabel="Bring back"
        confirmColor="warning"
        onCancel={() => setIncludeOpen(false)}
        onConfirm={() => {
          setIncludeOpen(false);
          handleRefresh([]);
        }}
      />
      {deleteOpen && (
        <DeleteSiteDialog
          open={deleteOpen}
          site={site}
          onClose={() => setDeleteOpen(false)}
          onConfirm={handleDelete}
        />
      )}
    </>
  );
}
