// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Auth API client.
 *
 * Talks to the nginx-gated `/api/v1/auth/*` endpoints. All requests send the
 * `cc_session` cookie automatically (httpOnly, SameSite=Strict). No tokens
 * are stored in JavaScript — authentication state is owned entirely by the
 * backend.
 */

import { apiClient } from './client';

// ========================================
// Types — wire shapes returned by the backend
// ========================================

/** Response from GET /auth/status. */
interface AuthStatusResponse {
  /** First-run setup required (no admin credential exists yet). */
  setup_needed: boolean;
  /** Current request has a valid session cookie. */
  authenticated: boolean;
  /** Username of the authenticated user, if any. */
  username: string | null;
}

/** The authenticated user. A single-user model — no id/email/role. */
export interface AuthUser {
  username: string;
}

/** API key list row — never includes the plaintext key or its hash. */
export interface ApiKeyInfo {
  id: string;
  name: string;
  created_at: string;
  last_used_at: string | null;
}

/** Response from POST /auth/keys — the ONLY time the plaintext key is shown. */
export interface ApiKeyCreatedResponse {
  id: string;
  name: string;
  /** Plaintext key — store this immediately, it is never retrievable again. */
  key: string;
  created_at: string;
}

// ========================================
// authApi — thin fetch wrappers
// ========================================

export const authApi = {
  /** GET /auth/status — public, used on app boot to decide setup/login routing. */
  getStatus: async (): Promise<AuthStatusResponse> => {
    const response = await apiClient.get<AuthStatusResponse>('/auth/status');
    return response.data;
  },

  /**
   * POST /auth/setup — first-run setup.
   *
   * Creates the admin credential and sets the session cookie. Returns 409 if
   * setup has already been completed.
   */
  setup: async (username: string, password: string): Promise<AuthUser> => {
    const response = await apiClient.post<AuthUser>('/auth/setup', {
      username,
      password,
    });
    return response.data;
  },

  /**
   * POST /auth/login — username + password authentication.
   *
   * On success, sets the `cc_session` cookie via Set-Cookie. Returns 401 on
   * bad credentials.
   */
  login: async (username: string, password: string): Promise<AuthUser> => {
    const response = await apiClient.post<AuthUser>('/auth/login', {
      username,
      password,
    });
    return response.data;
  },

  /** POST /auth/logout — clears the session cookie. Always returns 204. */
  logout: async (): Promise<void> => {
    await apiClient.post('/auth/logout');
  },

  /** GET /auth/me — returns the current user. 401 if no valid session. */
  getMe: async (): Promise<AuthUser> => {
    const response = await apiClient.get<AuthUser>('/auth/me');
    return response.data;
  },

  /**
   * POST /auth/password — change password.
   *
   * Clears the session cookie on success, forcing a re-login with the new
   * password.
   */
  changePassword: async (oldPassword: string, newPassword: string): Promise<void> => {
    await apiClient.post('/auth/password', {
      old_password: oldPassword,
      new_password: newPassword,
    });
  },

  /**
   * POST /auth/username — change username.
   *
   * Requires the current password for confirmation. Sets a fresh session
   * cookie so the user stays logged in.
   */
  changeUsername: async (password: string, newUsername: string): Promise<AuthUser> => {
    const response = await apiClient.post<AuthUser>('/auth/username', {
      password,
      new_username: newUsername,
    });
    return response.data;
  },

  /**
   * POST /auth/keys — create a new API key.
   *
   * The plaintext key in the response is shown ONCE. Callers MUST surface it
   * to the user immediately (e.g. a modal with copy-to-clipboard) because the
   * backend stores only the hash.
   */
  createKey: async (name: string): Promise<ApiKeyCreatedResponse> => {
    const response = await apiClient.post<ApiKeyCreatedResponse>('/auth/keys', { name });
    return response.data;
  },

  /** GET /auth/keys — list keys for the authenticated user. No hash, no plaintext. */
  listKeys: async (): Promise<ApiKeyInfo[]> => {
    const response = await apiClient.get<ApiKeyInfo[]>('/auth/keys');
    return response.data;
  },

  /** DELETE /auth/keys/{id} — revoke an API key. 404 if unknown. */
  revokeKey: async (keyId: string): Promise<void> => {
    await apiClient.delete(`/auth/keys/${keyId}`);
  },
};
