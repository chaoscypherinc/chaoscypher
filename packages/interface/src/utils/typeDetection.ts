// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Type detection utilities for property values.
 *
 * Provides smart detection of value types for type-aware rendering.
 */

type ValueType =
  | 'url'
  | 'email'
  | 'date'
  | 'boolean'
  | 'number'
  | 'array'
  | 'object'
  | 'longText'
  | 'text';

/**
 * URL pattern - matches http, https, ftp URLs
 */
const URL_PATTERN = /^(https?|ftp):\/\/[^\s/$.?#].[^\s]*$/i;

/**
 * Email pattern - basic email validation
 */
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * ISO date pattern - matches ISO 8601 dates
 */
const ISO_DATE_PATTERN = /^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}(\.\d{3})?(Z|[+-]\d{2}:\d{2})?)?$/;

/**
 * Long text threshold - strings longer than this are considered "long text"
 */
const LONG_TEXT_THRESHOLD = 100;

/**
 * Detect the type of a value for smart rendering.
 *
 * @param value - The value to detect type for
 * @returns The detected value type
 */
export function detectValueType(value: unknown): ValueType {
  // Null/undefined treated as text
  if (value === null || value === undefined) {
    return 'text';
  }

  // Boolean
  if (typeof value === 'boolean') {
    return 'boolean';
  }

  // Number
  if (typeof value === 'number') {
    return 'number';
  }

  // Array
  if (Array.isArray(value)) {
    return 'array';
  }

  // Object (but not array, not null)
  if (typeof value === 'object') {
    return 'object';
  }

  // String checks
  if (typeof value === 'string') {
    const trimmed = value.trim();

    // URL
    if (URL_PATTERN.test(trimmed)) {
      return 'url';
    }

    // Email
    if (EMAIL_PATTERN.test(trimmed)) {
      return 'email';
    }

    // Date (ISO format)
    if (ISO_DATE_PATTERN.test(trimmed)) {
      // Verify it's a valid date
      const date = new Date(trimmed);
      if (!isNaN(date.getTime())) {
        return 'date';
      }
    }

    // Long text
    if (trimmed.length > LONG_TEXT_THRESHOLD) {
      return 'longText';
    }

    // Default: regular text
    return 'text';
  }

  // Fallback
  return 'text';
}

