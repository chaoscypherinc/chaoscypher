// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { createContext } from 'react';

interface UploadDialogContextValue {
  openUploadDialog: () => void;
}

export const UploadDialogContext = createContext<UploadDialogContextValue | null>(null);
