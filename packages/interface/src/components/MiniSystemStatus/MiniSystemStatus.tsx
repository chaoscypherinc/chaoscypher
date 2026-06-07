// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * MiniSystemStatus: Sidebar system status indicator with dropdown menu.
 *
 * Orchestrates the status indicator pill, a hover/click dropdown menu with
 * health sections, knowledge counts, pause/resume toggle, and an add-source
 * quick action. Delegates data fetching to useSystemStatusData and rendering
 * of each section to focused sub-components.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Badge,
  Box,
  Button,
  Divider,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Snackbar,
  Tooltip,
  alpha,
} from '@mui/material';
import FactCheckIcon from '@mui/icons-material/FactCheck';
import PauseCircleIcon from '@mui/icons-material/PauseCircle';
import PlayCircleIcon from '@mui/icons-material/PlayCircle';
import { Upload } from 'lucide-react';

import { useNavigate } from 'react-router';
import { StatusColors } from '../../theme/colors';
import { ChaosCypherPalette } from '../../theme/palette';
import { useSystemStatusData } from './useSystemStatusData';
import { StatusIndicator } from './StatusIndicator';
import { HealthSections } from './HealthSections';
import { KnowledgeCounts } from './KnowledgeCounts';

interface MiniSystemStatusProps {
  /** Callback to open the Add Source / upload dialog. */
  onAddSource?: () => void;
  /** System-wide pause state from useSystemPauseStatus. */
  systemPauseStatus?: { paused: boolean; paused_at: string | null; reason: string | null };
  /** Callback to pause system-wide processing. */
  onPauseSystem?: (reason?: string) => Promise<void>;
  /** Callback to resume system-wide processing. */
  onResumeSystem?: () => Promise<void>;
}

/**
 * Sidebar system status widget.
 *
 * Shows a compact pill with a colored dot and summary text. On hover or click,
 * opens a dropdown with health details, knowledge counts, pause/resume, and
 * add-source actions.
 */
export default function MiniSystemStatus({
  onAddSource,
  systemPauseStatus,
  onPauseSystem,
  onResumeSystem,
}: MiniSystemStatusProps) {
  const [toastOpen, setToastOpen] = useState(false);
  const [menuAnchorEl, setMenuAnchorEl] = useState<null | HTMLElement>(null);
  const toastShownRef = useRef(false);
  const hoverCloseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const buttonRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  const {
    counts,
    loading,
    health,
    healthLoading,
    hasErrors,
    isActivityActive,
    getStatusText,
    awaitingConfirmationCount,
  } = useSystemStatusData();

  const isSystemPaused = systemPauseStatus?.paused ?? false;
  const shouldPulse = isActivityActive && !hasErrors && !isSystemPaused;

  // Fire the toast on each fresh error cycle. The latch (`toastShownRef`)
  // prevents re-firing while `hasErrors` stays true across polls, but it
  // is cleared once errors clear so the next degradation can alert again.
  // Intentional setState-in-effect: the toast must trigger as a side
  // effect of the polled `hasErrors` flag flipping true, not as a
  // memoized derivation.
  useEffect(() => {
    if (hasErrors && !toastShownRef.current) {
      toastShownRef.current = true;
      setToastOpen(true);
    } else if (!hasErrors) {
      // Reset the latch so a future degradation can re-alert the user.
      toastShownRef.current = false;
    }
  }, [hasErrors]);

  // Clean up hover close timer on unmount
  useEffect(() => {
    return () => {
      if (hoverCloseTimer.current) clearTimeout(hoverCloseTimer.current);
    };
  }, []);

  // --- Menu hover/click handlers ---

  const handleMouseEnter = useCallback(() => {
    if (hoverCloseTimer.current) {
      clearTimeout(hoverCloseTimer.current);
      hoverCloseTimer.current = null;
    }
    if (!menuAnchorEl && buttonRef.current) {
      setMenuAnchorEl(buttonRef.current);
    }
  }, [menuAnchorEl]);

  const handleMouseLeave = useCallback(() => {
    hoverCloseTimer.current = setTimeout(() => {
      setMenuAnchorEl(null);
      hoverCloseTimer.current = null;
    }, 200);
  }, []);

  const handleClick = useCallback(
    (event: React.MouseEvent<HTMLElement>) => {
      setMenuAnchorEl(menuAnchorEl ? null : event.currentTarget);
    },
    [menuAnchorEl],
  );

  const handleMenuClose = useCallback(() => {
    setMenuAnchorEl(null);
  }, []);

  const handleNavigate = useCallback(
    (path: string) => {
      navigate(path);
      handleMenuClose();
    },
    [navigate, handleMenuClose],
  );

  return (
    <>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
        <StatusIndicator
          loading={loading}
          healthLoading={healthLoading}
          isSystemPaused={isSystemPaused}
          shouldPulse={shouldPulse}
          statusText={getStatusText(isSystemPaused)}
          containerRef={buttonRef}
          onClick={handleClick}
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
        />
        {awaitingConfirmationCount > 0 && (
          <Tooltip
            title={`${awaitingConfirmationCount} source${awaitingConfirmationCount === 1 ? '' : 's'} awaiting confirmation`}
            arrow
          >
            <IconButton
              size="small"
              aria-label={`${awaitingConfirmationCount} source${awaitingConfirmationCount === 1 ? '' : 's'} awaiting confirmation`}
              onClick={() => navigate('/sources?status=awaiting_confirmation')}
              sx={{ color: 'info.main' }}
            >
              <Badge badgeContent={awaitingConfirmationCount} color="info">
                <FactCheckIcon sx={{ fontSize: 18 }} />
              </Badge>
            </IconButton>
          </Tooltip>
        )}
      </Box>

      {/* Dropdown Menu */}
      <Menu
        anchorEl={menuAnchorEl}
        open={Boolean(menuAnchorEl)}
        onClose={handleMenuClose}
        autoFocus={false}
        disableAutoFocusItem
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{
          pointerEvents: 'none',
          '& .MuiPaper-root': { pointerEvents: 'auto' },
          '& .MuiList-root': { py: 1 },
        }}
        slotProps={{
          paper: {
            onMouseEnter: handleMouseEnter,
            onMouseLeave: handleMouseLeave,
            sx: {
              mt: 0.5,
              borderRadius: 2,
              minWidth: 240,
              boxShadow: '0 12px 40px rgba(0,0,0,0.4)',
              border: '1px solid rgba(0, 229, 255, 0.1)',
              backgroundColor: 'rgba(5, 5, 10, 0.65) !important',
              backdropFilter: 'blur(16px)',
              WebkitBackdropFilter: 'blur(16px)',
              backgroundImage: 'none',
            },
          },
          transition: { timeout: { enter: 150, exit: 100 } },
        }}
      >
        {/* Health Sections */}
        {health && <HealthSections health={health} onNavigate={handleNavigate} />}

        {/* Knowledge Counts */}
        <KnowledgeCounts counts={counts} health={health} onNavigate={handleNavigate} />

        {/* Pause/Resume toggle */}
        {(onPauseSystem || onResumeSystem) && (
          <>
            <Divider sx={{ my: 0.5, borderColor: 'rgba(255, 255, 255, 0.06)' }} />
            <MenuItem
              onClick={async () => {
                if (isSystemPaused) {
                  await onResumeSystem?.();
                } else {
                  await onPauseSystem?.();
                }
                handleMenuClose();
              }}
              sx={{
                py: 1,
                px: 2,
                minHeight: 'auto',
                transition: 'all 0.15s ease-in-out',
                ...(isSystemPaused && { bgcolor: 'rgba(255, 152, 0, 0.06)' }),
                '&:hover': {
                  bgcolor: isSystemPaused
                    ? 'rgba(255, 152, 0, 0.12)'
                    : 'rgba(255, 152, 0, 0.05)',
                },
              }}
            >
              <ListItemIcon
                sx={{
                  minWidth: 28,
                  color: isSystemPaused ? StatusColors.warning : alpha('#fff', 0.25),
                }}
              >
                {isSystemPaused ? (
                  <PlayCircleIcon sx={{ fontSize: 18 }} />
                ) : (
                  <PauseCircleIcon sx={{ fontSize: 18 }} />
                )}
              </ListItemIcon>
              <ListItemText
                primary={isSystemPaused ? 'Resume Processing' : 'Pause Processing'}
                secondary={
                  isSystemPaused
                    ? systemPauseStatus?.reason
                      ? `Paused: ${systemPauseStatus.reason}`
                      : 'All processing is paused'
                    : 'Pause all source processing'
                }
                slotProps={{
                  primary: {
                    sx: {
                      fontSize: '0.8rem',
                      fontWeight: isSystemPaused ? 600 : 500,
                      color: isSystemPaused ? StatusColors.warning : undefined,
                    },
                  },
                  secondary: { noWrap: true, sx: { fontSize: '0.7rem' } },
                }}
              />
            </MenuItem>
          </>
        )}

        {/* Add Source action */}
        {onAddSource && (
          <>
            <Divider sx={{ my: 0.5, borderColor: 'rgba(255, 255, 255, 0.06)' }} />
            <MenuItem
              onClick={() => {
                onAddSource();
                handleMenuClose();
              }}
              sx={{
                py: 1,
                px: 2,
                minHeight: 'auto',
                transition: 'all 0.15s ease-in-out',
                '&:hover': { bgcolor: 'rgba(255, 0, 128, 0.05)' },
              }}
            >
              <ListItemIcon sx={{ minWidth: 28, color: ChaosCypherPalette.secondary }}>
                <Upload size={15} strokeWidth={1.5} />
              </ListItemIcon>
              <ListItemText
                primary="Add Source"
                secondary="Upload files or URLs"
                slotProps={{
                  primary: {
                    sx: {
                      fontSize: '0.8rem',
                      fontWeight: 600,
                      color: ChaosCypherPalette.secondary,
                    },
                  },
                  secondary: { sx: { fontSize: '0.7rem' } },
                }}
              />
            </MenuItem>
          </>
        )}
      </Menu>

      {/* One-time error toast */}
      <Snackbar
        open={toastOpen}
        autoHideDuration={8000}
        onClose={() => setToastOpen(false)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      >
        <Alert
          severity="error"
          variant="filled"
          onClose={() => setToastOpen(false)}
          action={
            <Button
              color="inherit"
              size="small"
              onClick={() => {
                setToastOpen(false);
                navigate('/settings');
              }}
            >
              Settings
            </Button>
          }
        >
          System health issue detected. Click for details.
        </Alert>
      </Snackbar>
    </>
  );
}
