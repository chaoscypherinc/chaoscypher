// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState, useEffect, useCallback, useMemo, type ReactNode } from 'react';
import { authApi, type AuthUser } from '../services/api/auth';
import { AuthContext } from './authContextValue';

/**
 * AuthProvider — single-cookie session state.
 *
 * The backend owns authentication. There are no tokens in JavaScript: the
 * browser sends the `cc_session` httpOnly cookie automatically on every
 * request, and nginx gates `/api/` via `auth_request`. All this provider
 * does is cache `/auth/status` + `/auth/me` for routing and UI decisions.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [needsSetup, setNeedsSetup] = useState(false);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  /**
   * Re-query the setup status from the backend.
   *
   * Called on boot and after setup completes. Also updates the authenticated
   * / user fields since `/auth/status` returns all three in one round-trip.
   */
  const recheckSetup = useCallback(async () => {
    try {
      const status = await authApi.getStatus();
      setNeedsSetup(status.setup_needed);
      setIsAuthenticated(status.authenticated);
      setUser(status.authenticated && status.username ? { username: status.username } : null);
    } catch {
      // Network / backend down — treat as unknown but don't block rendering.
      setNeedsSetup(false);
      setIsAuthenticated(false);
      setUser(null);
    }
  }, []);

  // Initialize auth state on mount.
  useEffect(() => {
    const initialize = async () => {
      await recheckSetup();
      setLoading(false);
    };
    initialize();
  }, [recheckSetup]);

  /**
   * Log in with credentials. The backend sets the `cc_session` cookie via
   * Set-Cookie on the response — the browser picks it up automatically.
   */
  const login = useCallback(async (username: string, password: string) => {
    const response = await authApi.login(username, password);
    setUser({ username: response.username });
    setIsAuthenticated(true);
    setNeedsSetup(false);
  }, []);

  /**
   * Log out — clears the session cookie on the backend, then clears local
   * state. Best-effort: we always clear local state even if the network call
   * fails (the cookie is httpOnly, so we can't clear it ourselves).
   */
  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } catch {
      // Best-effort — fall through and clear local state anyway.
    }
    setUser(null);
    setIsAuthenticated(false);
  }, []);

  /**
   * Mark setup as complete.
   *
   * The `/auth/setup` response already set the session cookie, so we just
   * need to re-query the backend for the now-authenticated user info.
   *
   * The state updates (needsSetup, isAuthenticated, user) all happen *after*
   * the `await` so they batch into a single render. Flipping needsSetup
   * before the await would cause an intermediate render where
   * `needsSetup=false` and `isAuthenticated=false` — AuthGuard would treat
   * that as "setup done, please log in" and redirect away from /setup,
   * breaking the multi-step wizard flow.
   */
  const completeSetup = useCallback(async () => {
    try {
      const me = await authApi.getMe();
      setUser(me);
      setIsAuthenticated(true);
      setNeedsSetup(false);
    } catch {
      // Setup response set the cookie but /me failed — recheck will fix it.
      await recheckSetup();
    }
  }, [recheckSetup]);

  const value = useMemo(
    () => ({
      needsSetup,
      isAuthenticated,
      user,
      loading,
      login,
      logout,
      completeSetup,
      recheckSetup,
    }),
    [needsSetup, isAuthenticated, user, loading, login, logout, completeSetup, recheckSetup],
  );

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}
