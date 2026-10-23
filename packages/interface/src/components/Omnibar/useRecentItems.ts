// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Hook for managing omnibar recent items in localStorage.
 * Uses useSyncExternalStore for cross-component reactivity.
 */
import { useCallback, useSyncExternalStore } from 'react';
import type { RecentItem } from './types';
import { readStoredJson, readStoredString, removeStoredItem, writeStoredJson } from '../../utils/storage';

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
  const raw = readStoredString(STORAGE_KEY);
  if (raw === cachedRaw) return cachedItems;
  cachedRaw = raw;
  cachedItems = readStoredJson<RecentItem[]>(STORAGE_KEY, EMPTY, isRecentItemList);
  return cachedItems;
}

function isRecentItemList(value: unknown): value is RecentItem[] {
  return Array.isArray(value);
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
    writeStoredJson(STORAGE_KEY, updated);
    emitChange();
  }, []);

  const clearRecentItems = useCallback(() => {
    removeStoredItem(STORAGE_KEY);
    emitChange();
  }, []);

  return { items, addRecentItem, clearRecentItems };
}
