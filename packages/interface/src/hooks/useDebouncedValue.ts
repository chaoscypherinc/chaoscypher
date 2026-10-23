// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useState } from 'react';

/**
 * `value`, once it has stopped changing for `delayMs`.
 *
 * Every change restarts the wait, so a field being typed into yields its
 * value only after the typing pauses (the Add Source dialog's site check).
 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}
