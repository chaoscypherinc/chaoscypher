// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Formatting utility functions
 * Centralized formatters for file sizes, durations, dates, numbers, etc.
 */

/**
 * Format large numbers with K/M suffixes for compact display.
 * @param num - The number to format
 * @param decimals - Decimal places for the suffix (default: 1)
 * @returns Formatted string (e.g., "1.5M", "23.4K", "999")
 */
export function formatCompactNumber(num: number, decimals = 1): string {
  if (num >= 1_000_000) return `${(num / 1_000_000).toFixed(decimals)}M`;
  if (num >= 1_000) return `${(num / 1_000).toFixed(decimals)}K`;
  return num.toString();
}

/**
 * Format bytes to human-readable file size
 * @param bytes - File size in bytes
 * @returns Formatted string (e.g., "1.5 MB")
 */
export function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 Bytes';

  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));

  return `${Math.round((bytes / Math.pow(k, i)) * 100) / 100} ${sizes[i]}`;
}

/**
 * Format ISO date string to localized string
 * @param dateString - ISO date string
 * @returns Localized date/time string
 */
export function formatDate(dateString: string): string {
  try {
    return new Date(dateString).toLocaleString();
  } catch (_error) {
    return dateString;
  }
}

/**
 * Format a duration in seconds to a human-readable string.
 * @param seconds - Duration in seconds
 * @returns Formatted string (e.g., "45s", "3m 12s", "1h 30m")
 */
export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${Math.floor(seconds % 60)}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

/**
 * Format a nullable duration in seconds to a human-readable string.
 * Supports sub-second values (displayed as ms). Returns null for invalid input.
 * @param seconds - Duration in seconds (can be null/undefined)
 * @returns Formatted string, or null if input is invalid
 */
export function formatDurationNullable(seconds: number | null | undefined): string | null {
  if (seconds == null || seconds <= 0) return null;
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  if (mins >= 60) {
    const hrs = Math.floor(mins / 60);
    const remainingMins = mins % 60;
    return remainingMins > 0 ? `${hrs}h ${remainingMins}m` : `${hrs}h`;
  }
  return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`;
}

/**
 * Format a duration in milliseconds to a human-readable string.
 * Handles null/undefined by returning a fallback string.
 * @param ms - Duration in milliseconds (or null/undefined)
 * @param fallback - String to return for null/undefined values (default: '-')
 * @returns Formatted string (e.g., "350ms", "2.5s", "1.3m", "1h 5m")
 */
export function formatDurationMs(ms: number | null | undefined, fallback = '-'): string {
  if (ms == null || ms <= 0) return fallback;
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 3600000) {
    const mins = Math.floor(ms / 60000);
    const secs = Math.floor((ms % 60000) / 1000);
    return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`;
  }
  const hrs = Math.floor(ms / 3600000);
  const mins = Math.floor((ms % 3600000) / 60000);
  return mins > 0 ? `${hrs}h ${mins}m` : `${hrs}h`;
}

/**
 * Format a timestamp string as a relative time (e.g., "5m ago", "2d ago").
 * Falls back to locale date string for timestamps older than a week.
 * @param timestamp - ISO timestamp string (or undefined)
 * @param fallback - String to return for missing timestamps (default: '-')
 * @returns Relative time string
 */
export function formatRelativeTime(timestamp?: string, fallback = '-'): string {
  if (!timestamp) return fallback;
  const date = new Date(timestamp);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString();
}

/**
 * Format a duration between two timestamps.
 * @param startedAt - Start time (ISO string or epoch seconds)
 * @param completedAt - End time (ISO string, epoch seconds, or null for "still running")
 * @returns Formatted duration string
 */
export function formatTaskDuration(
  startedAt: string | number,
  completedAt?: string | number | null,
): string {
  const startMs =
    typeof startedAt === 'string'
      ? new Date(startedAt).getTime()
      : startedAt * 1000;
  const endMs = completedAt
    ? typeof completedAt === 'string'
      ? new Date(completedAt).getTime()
      : completedAt * 1000
    : Date.now();
  return formatDuration(Math.floor((endMs - startMs) / 1000));
}

/**
 * Format a number with locale-specific formatting.
 * @param value - The number to format
 * @returns Formatted number string (e.g., "1,234,567")
 */
export function formatNumber(value: number): string {
  return new Intl.NumberFormat().format(value);
}

/**
 * Format a date relative to now (e.g., "2 days ago").
 * @param date - The Date object to format
 * @returns Relative time string
 */
export function formatRelativeDate(date: Date): string {
  const now = new Date();
  const diff = now.getTime() - date.getTime();

  const seconds = Math.floor(diff / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  const weeks = Math.floor(days / 7);
  const months = Math.floor(days / 30);
  const years = Math.floor(days / 365);

  if (seconds < 60) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  if (hours < 24) return `${hours}h ago`;
  if (days < 7) return `${days}d ago`;
  if (weeks < 4) return `${weeks}w ago`;
  if (months < 12) return `${months}mo ago`;
  return `${years}y ago`;
}

/**
 * Format a date as absolute (locale-specific).
 * @param date - The Date object to format
 * @returns Absolute date string
 */
export function formatAbsoluteDate(date: Date): string {
  return date.toLocaleString();
}

/**
 * Truncate a URL for display, preserving domain and path tail.
 * @param url - The URL to truncate
 * @param maxLength - Maximum length (default: 50)
 * @returns Truncated URL
 */
export function truncateUrl(url: string, maxLength: number = 50): string {
  if (url.length <= maxLength) return url;

  try {
    const parsed = new URL(url);
    const domain = parsed.hostname;
    const path = parsed.pathname;

    // If domain alone is too long, just truncate
    if (domain.length >= maxLength - 3) {
      return url.substring(0, maxLength - 3) + '...';
    }

    // Try to show domain + truncated path
    const remainingLength = maxLength - domain.length - 6; // 6 for "..." at start and end
    if (remainingLength > 10 && path.length > remainingLength) {
      return `${domain}/...${path.substring(path.length - remainingLength)}`;
    }

    return url.substring(0, maxLength - 3) + '...';
  } catch {
    // Invalid URL, just truncate
    return url.substring(0, maxLength - 3) + '...';
  }
}

/**
 * Truncate text with ellipsis.
 * @param text - The text to truncate
 * @param maxLength - Maximum length (default: 100)
 * @returns Truncated text
 */
export function truncateText(text: string, maxLength: number = 100): string {
  if (text.length <= maxLength) return text;
  return text.substring(0, maxLength - 3) + '...';
}

/**
 * Clean a system template type name for display.
 * Strips prefixes, replaces underscores, and title-cases words.
 * @param type - Raw type string (e.g., "system_template_person")
 * @returns Display name (e.g., "Person")
 */
export function cleanTypeName(type: string | null | undefined): string {
  if (!type) return 'Unknown';
  return type
    .replace('system_template_', '')
    .replace(/_/g, ' ')
    .split(' ')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

