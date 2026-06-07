// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Hook for managing omnibar recent items in localStorage.
 * Uses useSyncExternalStore for cross-component reactivity.
 */
import { useCallback, useSyncExternalStore } from 'react';
import type { RecentItem } from './types';

const STORAGE_KEY = 'chaoscypher-omnibar-recent';
const MAX_ITEMS = 5;
const EMPTY: RecentItem[] = [];

let listeners: Array<() => void> = [];

// Cache the parsed result so useSyncExternalStore gets a stable reference.
// useSyncExternalStore compares with Object.is — JSON.parse always returns
// a new reference, which would cause infinite re-renders.
let cachedRaw: string | null = null;
let cachedItems: RecentItem[] = EMPTY;

function emitChange() {
  // Invalidate cache so next getSnapshot reads fresh
  cachedRaw = null;
  for (const listener of listeners) {
    listener();
  }
}

function getSnapshot(): RecentItem[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === cachedRaw) return cachedItems;
    cachedRaw = raw;
    cachedItems = raw ? JSON.parse(raw) : EMPTY;
    return cachedItems;
  } catch {
    return EMPTY;
  }
}

function subscribe(listener: () => void): () => void {
  listeners = [...listeners, listener];
  return () => {
    listeners = listeners.filter((l) => l !== listener);
  };
}

export function useRecentItems() {
  const items = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  const addRecentItem = useCallback((item: Omit<RecentItem, 'timestamp'>) => {
    const current = getSnapshot();
    const filtered = current.filter((i) => !(i.id === item.id && i.type === item.type));
    const updated = [{ ...item, timestamp: Date.now() }, ...filtered].slice(0, MAX_ITEMS);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
    emitChange();
  }, []);

  const clearRecentItems = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY);
    emitChange();
  }, []);

  return { items, addRecentItem, clearRecentItems };
}
