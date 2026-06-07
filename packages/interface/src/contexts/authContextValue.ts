// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Auth context value type and React context object.
 *
 * Lives in its own file so AuthContext.tsx (component) and useAuth.ts (hook)
 * can both consume it without tripping the react-refresh
 * only-export-components rule.
 */
import { createContext } from 'react';
import type { AuthUser } from '../services/api/auth';

export interface AuthContextValue {
  /** Whether first-run setup is required. */
  needsSetup: boolean;
  /** Whether the user is currently authenticated. */
  isAuthenticated: boolean;
  /** The current user, or null if not authenticated. */
  user: AuthUser | null;
  /** Whether the auth state is still being determined. */
  loading: boolean;
  /** Log in with username and password. */
  login: (username: string, password: string) => Promise<void>;
  /** Log out and clear the session cookie. */
  logout: () => Promise<void>;
  /** Mark setup as completed (cookie set by backend setup response). */
  completeSetup: () => Promise<void>;
  /** Re-query the setup status from the backend. */
  recheckSetup: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);
