// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Generic CRUD API factory to eliminate duplication.
 * Creates type-safe API clients for resources.
 *
 * Backend response formats:
 * - List endpoints: {data: [...], pagination: {...}}
 * - Single item endpoints: direct object
 */
import type { ApiClient } from './api/client';

/**
 * Pagination metadata returned by backend list endpoints (uses page_size field)
 */
export interface PaginationMetadata {
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  has_next: boolean;
  has_prev: boolean;
}

/**
 * Backend list response format
 */
interface ListResponse<T> {
  data: T[];
  pagination?: PaginationMetadata;
}

/**
 * Generic CRUD API interface
 */
interface CrudApi<T, TCreate = Partial<T>, TUpdate = Partial<T>> {
  /**
   * List all resources with optional pagination
   * @param params Query parameters (filters, pagination)
   * @returns Array of resources with optional pagination metadata
   */
  list: (params?: Record<string, unknown>) => Promise<ListResponse<T>>;

  /**
   * Get a single resource by ID
   * @param id Resource ID
   * @returns Resource object
   */
  get: (id: string) => Promise<T>;

  /**
   * Create a new resource
   * @param data Resource creation data
   * @returns Created resource
   */
  create: (data: TCreate) => Promise<T>;

  /**
   * Update a resource (partial update using PATCH)
   * @param id Resource ID
   * @param updates Partial resource updates
   * @returns Updated resource
   */
  update: (id: string, updates: TUpdate) => Promise<T>;

  /**
   * Delete a resource
   * @param id Resource ID
   * @param params Optional delete parameters (e.g., force)
   */
  delete: (id: string, params?: Record<string, unknown>) => Promise<void>;
}

/**
 * Create a CRUD API client for a resource.
 *
 * @param endpoint - API endpoint (e.g., '/templates', '/nodes')
 * @param client - API client instance to use for requests
 * @returns CRUD API client
 */
export function createCrudApi<T, TCreate = Partial<T>, TUpdate = Partial<T>>(
  endpoint: string,
  client: ApiClient
): CrudApi<T, TCreate, TUpdate> {
  return {
    list: async (params?: Record<string, unknown>): Promise<ListResponse<T>> => {
      const response = await client.get(endpoint, { params });
      return {
        data: response.data.data as T[],
        pagination: response.data.pagination,
      };
    },

    get: async (id: string): Promise<T> => {
      const response = await client.get(`${endpoint}/${id}`);
      return response.data as T;
    },

    create: async (data: TCreate): Promise<T> => {
      const response = await client.post(endpoint, data);
      return response.data as T;
    },

    update: async (id: string, updates: TUpdate): Promise<T> => {
      const response = await client.patch(`${endpoint}/${id}`, updates);
      return response.data as T;
    },

    delete: async (id: string, params?: Record<string, unknown>): Promise<void> => {
      await client.delete(`${endpoint}/${id}`, { params });
    },
  };
}

/**
 * Fetch all pages of a paginated resource
 * Automatically loops through all pages and returns combined results
 *
 * @param fetchFn - Function that fetches a single page given page number
 * @param initialPageSize - Page size to use (default: 100)
 * @returns Combined array of all items across all pages
 */
export async function fetchAllPages<T>(
  fetchFn: (page: number, pageSize: number) => Promise<ListResponse<T>>,
  initialPageSize: number = 100
): Promise<T[]> {
  // Fetch page 1 to learn the total, then fetch remaining pages in parallel
  const firstResponse = await fetchFn(1, initialPageSize);
  const allItems: T[] = [...firstResponse.data];

  if (!firstResponse.pagination || !firstResponse.pagination.has_next) {
    return allItems;
  }

  const total = firstResponse.pagination.total;
  const totalPages = Math.ceil(total / initialPageSize);

  if (totalPages <= 1) return allItems;

  // Fetch pages 2..N in parallel
  const pagePromises: Promise<ListResponse<T>>[] = [];
  for (let page = 2; page <= totalPages; page++) {
    pagePromises.push(fetchFn(page, initialPageSize));
  }

  const responses = await Promise.all(pagePromises);
  for (const response of responses) {
    allItems.push(...response.data);
  }

  return allItems;
}
