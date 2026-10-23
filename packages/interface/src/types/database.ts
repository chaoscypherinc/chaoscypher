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

/** `GET /databases/current`: the active database, and its one-line notice when it has one. */
export interface CurrentDatabase {
  current: string;
  info: DatabaseInfo;
  /** Set for a database made before the core entity types (export, update, import); else null. */
  notice?: string | null;
}

export interface DatabaseCreate {
  name: string;
}
