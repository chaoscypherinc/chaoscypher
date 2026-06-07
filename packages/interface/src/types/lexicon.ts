// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

// TypeScript types for Lexicon (Package Registry) API

// ============================================================================
// Auth Types
// ============================================================================

export interface LexiconAuthStatus {
  authenticated: boolean;
  username: string | null;
  lexicon_url: string | null;
  token_present: boolean;
}

export interface LexiconDeviceCodeRequest {
  lexicon_url?: string;
  client_id?: string;
  scope?: string;
}

export interface LexiconDeviceCodeResponse {
  device_code: string;
  user_code: string;
  verification_uri: string;
  verification_uri_complete: string | null;
  expires_in: number;
  interval: number;
}

export interface LexiconPollRequest {
  device_code: string;
  lexicon_url?: string;
  client_id?: string;
}

export interface LexiconAuthResponse {
  success: boolean;
  username: string | null;
  lexicon_url: string;
  message: string;
}

// ============================================================================
// Package Types
// ============================================================================

export type PackageType = 'FULL' | 'TEMPLATES' | 'KNOWLEDGE' | 'LENSES' | 'WORKFLOWS' | 'MIXED';

export interface LexiconPackageInfo {
  id: string;
  name: string;
  description: string;
  owner_username: string;
  owner_name: string;
  owner_id: string;
  is_public: boolean;
  package_type: PackageType;
  star_count: number;
  version_count: number;
  download_count: number;
  created_at: number;  // Unix timestamp (ms)
  updated_at: number;  // Unix timestamp (ms)
}

export interface LexiconSearchParams {
  query?: string;
  page?: number;
  limit?: number;
  sort_by?: SortOption;
  is_public?: boolean;
  owner_id?: string;
  package_type?: PackageType;
}

export interface LexiconSearchResponse {
  packages: LexiconPackageInfo[];
  total: number;
  page: number;
  limit: number;
}

// ============================================================================
// UI State Types
// ============================================================================

export type ViewMode = 'cards' | 'table';

export type SortOption = 'relevance' | 'stars' | 'downloads' | 'newest' | 'updated' | 'name';
