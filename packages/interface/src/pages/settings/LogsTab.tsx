// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Box,
  Typography,
  Tab,
  Tabs,
  Button,
  Alert,
  CircularProgress,
  Select,
  MenuItem,
  Tooltip,
} from '@mui/material';
import type { SelectChangeEvent } from '@mui/material';
import PauseIcon from '@mui/icons-material/Pause';
import PlayIcon from '@mui/icons-material/PlayArrow';
import DownloadIcon from '@mui/icons-material/Download';
import EventsIcon from '@mui/icons-material/EventNote';
import { useLogViewer } from './hooks/useLogViewer';
import type { ServiceTab } from './hooks/useLogViewer';
import EventsTab from './EventsTab';
import { LogPane } from './components/LogPane';
import { ServiceStatusBar } from './components/ServiceStatusBar';
import { useLogLevel, useSetLogLevel, useExportDiagnostics } from './hooks/useLogLevel';
import { ghostButtonSx } from '../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../theme/palette';
import type { Settings } from '../../types';

const SERVICE_TABS: ServiceTab[] = ['all', 'cortex', 'neuron', 'nginx', 'valkey'];

interface LogsTabProps {
  settings: Settings | null;
  setSettings: (settings: Settings) => void;
}

/** Sub-tab identifiers: 'events' plus all service log tabs. */
const SUB_TABS = ['events', ...SERVICE_TABS] as const;
type SubTab = (typeof SUB_TABS)[number];

export default function LogsTab(_props: LogsTabProps) {
  const {
    activeTab,
    setActiveTab,
    lines,
    totalLines,
    status,
    loading,
    paused,
    togglePause,
    error,
  } = useLogViewer();

  const [subTab, setSubTab] = useState<SubTab>('events');
  const showEvents = subTab === 'events';

  const logPaneRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const [exportError, setExportError] = useState<string | null>(null);

  const { data: logLevelData } = useLogLevel();
  const logLevel = logLevelData?.level ?? 'INFO';
  const availableLevels = logLevelData?.available_levels ?? [];

  const setLogLevelMutation = useSetLogLevel();
  const exportDiagnostics = useExportDiagnostics();
  const exporting = exportDiagnostics.isPending;

  const handleLogLevelChange = useCallback(async (event: SelectChangeEvent<string>) => {
    const newLevel = event.target.value;
    try {
      await setLogLevelMutation.mutateAsync(newLevel);
    } catch {
      // Silently fail — level unchanged
    }
  }, [setLogLevelMutation]);

  // Auto-scroll to bottom
  useEffect(() => {
    if (autoScroll && logPaneRef.current) {
      logPaneRef.current.scrollTop = logPaneRef.current.scrollHeight;
    }
  }, [lines, autoScroll]);

  // Detect user scroll to disable auto-scroll
  const handleScroll = () => {
    if (!logPaneRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = logPaneRef.current;
    const isAtBottom = scrollHeight - scrollTop - clientHeight < 40;
    setAutoScroll(isAtBottom);
  };

  const handleExport = async () => {
    setExportError(null);
    try {
      await exportDiagnostics.mutateAsync();
    } catch {
      setExportError('Failed to export diagnostic bundle');
    }
  };

  const subTabIndex = SUB_TABS.indexOf(subTab);

  const handleSubTabChange = (_: React.SyntheticEvent, newValue: number) => {
    const selected = SUB_TABS[newValue];
    setSubTab(selected);
    if (selected !== 'events') {
      setActiveTab(selected as ServiceTab);
    }
  };

  return (
    <Box sx={{ p: 3 }}>
      {error && !showEvents && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}
      {exportError && !showEvents && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setExportError(null)}>
          {exportError}
        </Alert>
      )}

      {/* Sub-tabs: Events + Service Logs + Controls */}
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          borderBottom: 1,
          borderColor: 'rgba(255,255,255,0.08)',
        }}
      >
        <Tabs
          value={subTabIndex}
          onChange={handleSubTabChange}
          variant="scrollable"
          scrollButtons={false}
          sx={{
            minHeight: 36,
            '& .MuiTab-root': {
              minHeight: 36,
              textTransform: 'none',
              fontSize: '0.8rem',
              px: 2,
              py: 0.5,
            },
          }}
        >
          <Tab
            icon={<EventsIcon sx={{ fontSize: 14 }} />}
            iconPosition="start"
            label="Events"
            sx={{
              color: `${ChaosCypherPalette.orange} !important`,
              '&.Mui-selected': { color: `${ChaosCypherPalette.orange} !important` },
            }}
          />
          {SERVICE_TABS.map((tab) => (
            <Tab
              key={tab}
              label={tab === 'all' ? 'All' : tab.charAt(0).toUpperCase() + tab.slice(1)}
            />
          ))}
        </Tabs>
        {!showEvents && (
          <>
            <Box sx={{ flex: 1 }} />
            <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.3)', mr: 1 }}>
              Auto-refresh: 3s
            </Typography>
            <Button
              size="small"
              onClick={togglePause}
              startIcon={
                paused ? <PlayIcon sx={{ fontSize: 14 }} /> : <PauseIcon sx={{ fontSize: 14 }} />
              }
              sx={{
                ...ghostButtonSx(ChaosCypherPalette.primary),
                textTransform: 'none',
                fontSize: '0.75rem',
                py: 0.25,
                px: 1,
                minWidth: 'auto',
              }}
            >
              {paused ? 'Resume' : 'Pause'}
            </Button>
          </>
        )}
      </Box>

      {/* Events sub-tab content */}
      {showEvents && <EventsTab />}

      {/* Service log sub-tab content */}
      {!showEvents && (
        <>
          {/* Application Log Level (above log box) */}
          {availableLevels.length > 0 && (
            <Box
              sx={{
                p: 2,
                border: '1px solid rgba(255,255,255,0.06)',
                borderTop: 'none',
                background: 'rgba(255,255,255,0.02)',
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <Box>
                  <Typography variant="subtitle2" sx={{ fontWeight: 600, color: 'rgba(255,255,255,0.85)' }}>
                    Application Log Level
                  </Typography>
                  <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.4)' }}>
                    Controls log output for Cortex and Neuron. Changes take effect immediately.
                  </Typography>
                </Box>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                  <Select
                    value={logLevel}
                    onChange={handleLogLevelChange}
                    size="small"
                    sx={{
                      fontSize: '0.8rem',
                      color: 'primary.main',
                      fontWeight: 600,
                      minWidth: 120,
                      '& .MuiOutlinedInput-notchedOutline': {
                        borderColor: 'rgba(0,229,255,0.2)',
                      },
                      '&:hover .MuiOutlinedInput-notchedOutline': {
                        borderColor: 'rgba(0,229,255,0.4)',
                      },
                      '& .MuiSelect-icon': { color: 'rgba(0,229,255,0.5)' },
                    }}
                  >
                    {availableLevels.map((lvl) => (
                      <MenuItem key={lvl} value={lvl} sx={{ fontSize: '0.8rem' }}>
                        {lvl}
                      </MenuItem>
                    ))}
                  </Select>
                  <Tooltip
                    title="Nginx and Valkey log levels are set via environment variables (NGINX_LOGLEVEL, VALKEY_LOGLEVEL) and require a container restart."
                    arrow
                    placement="left"
                  >
                    <Typography
                      variant="caption"
                      sx={{
                        color: 'rgba(255,255,255,0.25)',
                        cursor: 'help',
                        borderBottom: '1px dotted rgba(255,255,255,0.2)',
                      }}
                    >
                      ?
                    </Typography>
                  </Tooltip>
                </Box>
              </Box>
            </Box>
          )}

          {/* Log Pane */}
          <LogPane
            lines={lines}
            loading={loading}
            activeTab={activeTab}
            logPaneRef={logPaneRef}
            onScroll={handleScroll}
          />

          {/* Status Bar */}
          {status?.available && status.services.length > 0 && (
            <ServiceStatusBar services={status.services} />
          )}

          {/* Bottom Bar */}
          <Box
            sx={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              mt: 1.5,
            }}
          >
            <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.3)' }}>
              Showing {lines.length.toLocaleString()} of {totalLines.toLocaleString()} lines
            </Typography>
            <Button
              size="small"
              onClick={handleExport}
              disabled={exporting}
              startIcon={
                exporting ? (
                  <CircularProgress size={12} />
                ) : (
                  <DownloadIcon sx={{ fontSize: 14 }} />
                )
              }
              sx={{
                ...ghostButtonSx(ChaosCypherPalette.success),
                textTransform: 'none',
                fontSize: '0.8rem',
                py: 0.5,
              }}
            >
              {exporting ? 'Exporting...' : 'Export Diagnostic Bundle'}
            </Button>
          </Box>
        </>
      )}
    </Box>
  );
}
