// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

// Database domain type definitions for Chaos Cypher frontend

export interface DatabaseInfo {
  name: string;
  path: string;
  exists: boolean;
  size: number;
  last_modified: string | null;
}

export interface DatabaseCreate {
  name: string;
}
