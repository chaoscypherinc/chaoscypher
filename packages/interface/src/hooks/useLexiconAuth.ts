// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState, useCallback, useEffect, useRef } from 'react';
import { lexiconApi } from '../services/api/lexicon';
import type {
  LexiconAuthStatus,
  LexiconDeviceCodeResponse,
} from '../types/lexicon';
import { logger } from '../utils/logger';

interface UseLexiconAuthReturn {
  authStatus: LexiconAuthStatus | null;
  loading: boolean;
  deviceCode: LexiconDeviceCodeResponse | null;
  polling: boolean;
  error: string | null;
  startDeviceAuth: () => Promise<void>;
  logout: () => Promise<void>;
  cancelAuth: () => void;
  refreshStatus: () => Promise<void>;
}

export function useLexiconAuth(): UseLexiconAuthReturn {
  const [authStatus, setAuthStatus] = useState<LexiconAuthStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [deviceCode, setDeviceCode] = useState<LexiconDeviceCodeResponse | null>(null);
  const [polling, setPolling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollingRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollingActiveRef = useRef(false);

  const refreshStatus = useCallback(async () => {
    try {
      setLoading(true);
      const status = await lexiconApi.getAuthStatus();
      setAuthStatus(status);
      setError(null);
    } catch (err) {
      setError('Failed to check authentication status');
      logger.error('Auth status error:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshStatus();
    return () => {
      if (pollingRef.current) {
        clearTimeout(pollingRef.current);
      }
    };
  }, [refreshStatus]);

  const startDeviceAuth = useCallback(async () => {
    // Cancel any in-flight polling before starting a new chain
    pollingActiveRef.current = false;
    if (pollingRef.current) {
      clearTimeout(pollingRef.current);
      pollingRef.current = null;
    }

    try {
      setError(null);
      const response = await lexiconApi.requestDeviceCode();
      setDeviceCode(response);
      setPolling(true);
      pollingActiveRef.current = true;

      // Start polling automatically
      const pollInterval = response.interval * 1000;
      const pollUntilSuccess = async () => {
        try {
          const pollResponse = await lexiconApi.pollDeviceToken({
            device_code: response.device_code,
          });

          if (pollResponse.success) {
            pollingActiveRef.current = false;
            setPolling(false);
            setDeviceCode(null);
            await refreshStatus();
            return;
          }

          // Continue polling (use ref to avoid stale closure)
          if (pollingActiveRef.current) {
            pollingRef.current = setTimeout(pollUntilSuccess, pollInterval);
          }
        } catch (err: unknown) {
          // Check if it's an expected "pending" error
          const status = err instanceof Object && 'response' in err
            ? (err as { response?: { status?: number } }).response?.status
            : undefined;
          if (status === 408) {
            // Device code expired
            pollingActiveRef.current = false;
            setPolling(false);
            setDeviceCode(null);
            setError('Authorization expired. Please try again.');
            return;
          }
          // Continue polling on other errors (use ref to avoid stale closure)
          if (pollingActiveRef.current) {
            pollingRef.current = setTimeout(pollUntilSuccess, pollInterval);
          }
        }
      };

      pollingRef.current = setTimeout(pollUntilSuccess, pollInterval);
    } catch (err) {
      setError('Failed to start authentication');
      logger.error('Device auth error:', err);
    }
  }, [refreshStatus]);

  const logout = useCallback(async () => {
    try {
      setLoading(true);
      await lexiconApi.logout();
      await refreshStatus();
    } catch (err) {
      setError('Failed to logout');
      logger.error('Logout error:', err);
    } finally {
      setLoading(false);
    }
  }, [refreshStatus]);

  const cancelAuth = useCallback(() => {
    pollingActiveRef.current = false;
    if (pollingRef.current) {
      clearTimeout(pollingRef.current);
    }
    setPolling(false);
    setDeviceCode(null);
    setError(null);
  }, []);

  return {
    authStatus,
    loading,
    deviceCode,
    polling,
    error,
    startDeviceAuth,
    logout,
    cancelAuth,
    refreshStatus,
  };
}
