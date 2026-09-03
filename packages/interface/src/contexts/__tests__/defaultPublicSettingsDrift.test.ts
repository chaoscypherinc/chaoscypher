// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { describe, it, expect } from 'vitest';

import fromJson from '../../../../cortex/tests/unit/features/settings_public/frontend_defaults.json';

import { DEFAULT_PUBLIC_SETTINGS } from '../publicSettingsContextValue';

/**
 * Closes the drift triangle: the backend test
 * `packages/cortex/tests/unit/features/settings_public/test_default_drift.py`
 * compares Pydantic defaults against `frontend_defaults.json`, and this test
 * compares that same JSON against the shipped TS constant. Together they
 * guarantee backend and frontend defaults agree.
 */
describe('DEFAULT_PUBLIC_SETTINGS drift guard', () => {
  it('matches frontend_defaults.json exactly', () => {
    expect(DEFAULT_PUBLIC_SETTINGS).toEqual(fromJson);
  });
});
