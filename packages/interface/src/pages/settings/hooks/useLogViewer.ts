// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState, useCallback, useRef } from 'react';
import { useAppConfig } from '../../../contexts/useAppConfig';
import { logsApi } from '../../../services/api/logs';
import { usePolling } from '../../../hooks/usePolling';
import type { LogResponse, ServiceStatusResponse } from '../../../services/api/logs';

export type ServiceTab = 'all' | 'cortex' | 'neuron' | 'nginx' | 'valkey';

interface LogViewerState {
  activeTab: ServiceTab;
  setActiveTab: (tab: ServiceTab) => void;
  lines: string[];
  totalLines: number;
  status: ServiceStatusResponse | null;
  loading: boolean;
  paused: boolean;
  togglePause: () => void;
  error: string | null;
}

export function useLogViewer(): LogViewerState {
  const config = useAppConfig();
  const POLL_INTERVAL_MS = config.intervals_log_poll_ms;
  const STATUS_POLL_INTERVAL_MS = config.intervals_status_poll_ms;
  const INITIAL_LINES = config.intervals_log_initial_lines;
  const POLL_LINES = config.intervals_log_poll_lines;

  const [activeTab, setActiveTab] = useState<ServiceTab>('all');
  const [lines, setLines] = useState<string[]>([]);
  const [totalLines, setTotalLines] = useState(0);
  const [status, setStatus] = useState<ServiceStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [paused, setPaused] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isInitialFetch = useRef(true);

  const fetchLogs = useCallback(async () => {
    const lineCount = isInitialFetch.current ? INITIAL_LINES : POLL_LINES;
    isInitialFetch.current = false;
    try {
      let response: LogResponse;
      if (activeTab === 'all') {
        response = await logsApi.getAll(lineCount);
      } else {
        response = await logsApi.getService(activeTab, lineCount);
      }
      setLines(response.lines);
      setTotalLines(response.total_lines);
      setError(null);
    } catch {
      setError('Failed to fetch logs');
    } finally {
      setLoading(false);
    }
  }, [activeTab, INITIAL_LINES, POLL_LINES]);

  const fetchStatus = useCallback(async () => {
    try {
      const response = await logsApi.getStatus();
      setStatus(response);
    } catch {
      // Status fetch failure is non-critical
    }
  }, []);

  // Reset initial fetch flag when tab changes
  const handleSetActiveTab = useCallback((tab: ServiceTab) => {
    isInitialFetch.current = true;
    setLoading(true);
    setActiveTab(tab);
  }, []);

  // Log polling — pauses when hidden and when user pauses
  usePolling({
    onPoll: fetchLogs,
    interval: POLL_INTERVAL_MS,
    enabled: !paused,
    pauseWhenHidden: true,
    immediate: true,
  });

  // Status polling — always active, pauses when hidden
  usePolling({
    onPoll: fetchStatus,
    interval: STATUS_POLL_INTERVAL_MS,
    enabled: true,
    pauseWhenHidden: true,
    immediate: true,
  });

  const togglePause = useCallback(() => {
    setPaused((prev) => !prev);
  }, []);

  return {
    activeTab,
    setActiveTab: handleSetActiveTab,
    lines,
    totalLines,
    status,
    loading,
    paused,
    togglePause,
    error,
  };
}
