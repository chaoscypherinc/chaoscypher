// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Fetch-based API client for Chaos Cypher.
 *
 * Replaces axios with native fetch while preserving the same consumer API:
 * `apiClient.get(url, config)`, `apiClient.post(url, data, config)`, etc.
 * Responses always include a `.data` property for drop-in compatibility.
 */

import { DEFAULT_PUBLIC_SETTINGS } from '../../contexts/publicSettingsContextValue';
import { logger } from '../../utils/logger';

// ========================================
// Client Configuration
// ========================================

export const API_BASE = import.meta.env.VITE_API_BASE || '/api/v1';

/**
 * Default request timeout in milliseconds. Sourced from
 * `DEFAULT_PUBLIC_SETTINGS.http_default_timeout_ms` so the value
 * matches the backend Pydantic default. Per-request override via
 * `RequestConfig.timeout`.
 */
const DEFAULT_TIMEOUT_MS = DEFAULT_PUBLIC_SETTINGS.http_default_timeout_ms;

// ========================================
// Types
// ========================================

/** Configuration for individual requests. */
export interface RequestConfig {
  params?: Record<string, unknown> | object;
  headers?: Record<string, string>;
  timeout?: number;
  signal?: AbortSignal;
  responseType?: 'json' | 'blob' | 'text';
  /** Request body for methods that support it (used by DELETE with body). */
  data?: unknown;
  /** Upload progress callback (only works with XMLHttpRequest fallback for FormData). */
  onUploadProgress?: (event: { loaded: number; total?: number }) => void;
}

/** Response shape returned by all client methods. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- Matches axios's AxiosResponse<T = any> default for backward compat
export interface ApiResponse<T = any> {
  data: T;
  status: number;
  headers: Headers;
}

/** Internal request config passed to interceptors and retry logic. */
export interface InternalRequestConfig extends RequestConfig {
  url: string;
  method: string;
  body?: unknown;
  /** Flag to prevent infinite retry loops. */
  _retry?: boolean;
}

/** Response interceptor function signature. */
type ResponseInterceptor = (
  response: ApiResponse,
  config: InternalRequestConfig,
) => Promise<ApiResponse>;

/** Error interceptor function signature. */
type ErrorInterceptor = (
  error: ApiClientError,
  config: InternalRequestConfig,
) => Promise<ApiResponse>;

// ========================================
// Normalized API Error
// ========================================

/**
 * Consistent error shape produced by the response interceptor.
 * Callers can check `error.isApiError` to access typed fields.
 */
interface ApiError {
  /** Discriminator flag for type narrowing. */
  isApiError: true;
  /** HTTP status code, or `null` for network / timeout errors. */
  status: number | null;
  /** Human-readable error summary. */
  message: string;
  /** Category for programmatic branching. */
  code: 'UNAUTHORIZED' | 'FORBIDDEN' | 'NOT_FOUND' | 'PAYLOAD_TOO_LARGE' | 'SERVER_ERROR' | 'NETWORK_ERROR' | 'TIMEOUT' | 'UNKNOWN';
  /** Raw detail from the server response body, if available. */
  detail?: string;
}

/**
 * Error class thrown by the API client. Extends Error and includes
 * the normalized ApiError fields plus the original response data.
 */
class ApiClientError extends Error implements ApiError {
  isApiError = true as const;
  status: number | null;
  code: ApiError['code'];
  detail?: string;
  response?: { data?: unknown; status?: number };
  config?: InternalRequestConfig;

  constructor(apiError: ApiError, response?: { data?: unknown; status?: number }, config?: InternalRequestConfig) {
    super(apiError.message);
    this.name = 'ApiClientError';
    this.status = apiError.status;
    this.code = apiError.code;
    this.detail = apiError.detail;
    this.response = response;
    this.config = config;
  }
}

/**
 * Type guard for checking if an error is an ApiClientError.
 * Drop-in replacement for axios `isAxiosError()`.
 */
export function isApiError(error: unknown): error is ApiClientError {
  return (
    error instanceof ApiClientError ||
    (error != null && typeof error === 'object' && (error as ApiError).isApiError === true)
  );
}

// ========================================
// Parameter Serialization
// ========================================

/**
 * Serialize query parameters to a URL search string.
 * Arrays are serialized as `key=a&key=b` (FastAPI format, no brackets).
 */
function serializeParams(params: Record<string, unknown> | object): string {
  const parts: string[] = [];

  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null) continue;

    if (Array.isArray(value)) {
      for (const item of value) {
        if (item !== undefined && item !== null) {
          parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(item))}`);
        }
      }
    } else {
      parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
    }
  }

  return parts.join('&');
}

// ========================================
// 401 → /login Redirect
// ========================================

/** Paths that should NOT trigger a 401 → /login redirect (avoid loops). */
const AUTH_PUBLIC_PATHS = new Set(['/login', '/setup']);

/** API endpoints whose 401 is meaningful to the caller, not a session problem. */
const AUTH_ENDPOINTS_WITH_EXPECTED_401 = [
  '/auth/login',
  '/auth/status',
  '/auth/me',
];

/**
 * Redirect the browser to /login?next=<current> when an API call returns 401
 * and we're currently on an authenticated page. No-op on /login, /setup, or
 * for auth endpoints that legitimately return 401 (the caller handles those).
 */
function maybeRedirectOnUnauthorized(status: number, requestUrl: string): void {
  if (status !== 401) return;
  if (typeof window === 'undefined') return;

  const currentPath = window.location.pathname;
  if (AUTH_PUBLIC_PATHS.has(currentPath)) return;

  if (AUTH_ENDPOINTS_WITH_EXPECTED_401.some((ep) => requestUrl.startsWith(ep))) {
    return;
  }

  const next = encodeURIComponent(window.location.pathname + window.location.search);
  window.location.href = `/login?next=${next}`;
}

// ========================================
// Error Normalization
// ========================================

/**
 * Extract a human-readable detail string from an error response body.
 *
 * The backend emits a unified error envelope since 2026-04-18:
 * ``{error: <CODE>, message: <human>, details?: <object>}``. The legacy
 * FastAPI ``{detail: ...}`` shape may still appear for edge cases (e.g.
 * exceptions raised before the custom http_exception_handler runs), so
 * we fall through to the old fields as well.
 *
 * Precedence:
 *   1. ``message`` (unified envelope — human-readable)
 *   2. ``detail.message`` (legacy nested ErrorDetail)
 *   3. ``detail`` when string (oldest FastAPI default)
 *   4. ``error`` (unified envelope — machine code, last resort)
 */
function extractDetail(data: unknown): string | undefined {
  if (!data || typeof data !== 'object') return undefined;
  const body = data as Record<string, unknown>;
  if (typeof body.message === 'string') return body.message;
  if (body.detail && typeof body.detail === 'object') {
    const nested = body.detail as Record<string, unknown>;
    if (typeof nested.message === 'string') return nested.message;
  }
  if (typeof body.detail === 'string') return body.detail;
  if (typeof body.error === 'string') return body.error;
  return undefined;
}

/**
 * Build a normalized ApiError from a failed response or network error.
 */
function normalizeError(
  status: number | null,
  data: unknown,
  errorMessage?: string,
): ApiError {
  // --- Timeout ---
  if (errorMessage?.includes('timeout') || errorMessage?.includes('aborted')) {
    return {
      isApiError: true,
      status: null,
      message: 'Request timed out. The server may be busy — please try again.',
      code: 'TIMEOUT',
    };
  }

  // --- Server responded with an error status ---
  if (status !== null) {
    const detail = extractDetail(data);

    if (status === 401) {
      logger.warn('[API] Unauthorized request (401)', detail);
      return { isApiError: true, status, message: 'Unauthorized. Please check your credentials.', code: 'UNAUTHORIZED', detail };
    }

    if (status === 403) {
      logger.warn('[API] Forbidden (403)', detail);
      return { isApiError: true, status, message: 'Access denied. You do not have permission for this action.', code: 'FORBIDDEN', detail };
    }

    if (status === 404) {
      logger.warn('[API] Not found (404)', detail);
      return { isApiError: true, status, message: 'The requested resource was not found.', code: 'NOT_FOUND', detail };
    }

    if (status === 413) {
      // 413 most often comes straight from nginx (client_max_body_size) on
      // the source-upload route, in which case the response body is the
      // branded HTML error page rather than the JSON envelope and `detail`
      // is undefined. Surface the size-specific message anyway so the
      // upload UI doesn't fall through to the generic SERVER_ERROR copy.
      logger.warn('[API] Payload too large (413)', detail);
      return {
        isApiError: true,
        status,
        message: detail || 'File too large for upload. Reduce the file size or split it into smaller pieces.',
        code: 'PAYLOAD_TOO_LARGE',
        detail,
      };
    }

    if (status >= 500) {
      logger.error(`[API] Server error (${status})`, detail);
      return { isApiError: true, status, message: 'A server error occurred. Please try again later.', code: 'SERVER_ERROR', detail };
    }

    // Other 4xx errors
    logger.warn(`[API] Request failed (${status})`, detail);
    return { isApiError: true, status, message: detail || `Request failed with status ${status}.`, code: 'UNKNOWN', detail };
  }

  // --- No response at all (network down, DNS failure, CORS, etc.) ---
  if (errorMessage) {
    logger.error('[API] Network error — no response received', errorMessage);
    return { isApiError: true, status: null, message: 'Network error. Please check your connection and try again.', code: 'NETWORK_ERROR' };
  }

  // --- Unknown ---
  logger.error('[API] Unexpected error');
  return { isApiError: true, status: null, message: 'An unexpected error occurred.', code: 'UNKNOWN' };
}

// ========================================
// Fetch-Based API Client
// ========================================

/** Registered response interceptors. */
const responseInterceptors: Array<{
  id: number;
  onFulfilled?: ResponseInterceptor;
  onRejected?: ErrorInterceptor;
}> = [];

let nextInterceptorId = 0;

/**
 * Execute a fetch request with timeout, error normalization, and interceptors.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- Default any matches axios convention
async function executeRequest<T = any>(config: InternalRequestConfig): Promise<ApiResponse<T>> {
  const { url, method, body, params, headers: extraHeaders, timeout, signal: externalSignal, responseType, onUploadProgress } = config;

  // Build full URL
  let fullUrl = `${API_BASE}${url}`;
  if (params) {
    const queryString = serializeParams(params);
    if (queryString) {
      fullUrl += `?${queryString}`;
    }
  }

  // Merge headers
  const headers: Record<string, string> = { ...extraHeaders };

  // Determine body and content type
  let fetchBody: BodyInit | undefined;

  if (body !== undefined && body !== null) {
    if (body instanceof FormData) {
      // Let the browser set the Content-Type with boundary for multipart
      delete headers['Content-Type'];
      fetchBody = body;
    } else if (typeof body === 'string') {
      fetchBody = body;
    } else {
      // JSON body
      if (!headers['Content-Type']) {
        headers['Content-Type'] = 'application/json';
      }
      fetchBody = JSON.stringify(body);
    }
  } else if (!headers['Content-Type'] && (method === 'POST' || method === 'PUT' || method === 'PATCH')) {
    // Set default content type for methods that typically have a body
    headers['Content-Type'] = 'application/json';
  }

  // Timeout via AbortController
  const effectiveTimeout = timeout ?? DEFAULT_TIMEOUT_MS;
  const timeoutController = new AbortController();
  const timeoutId = setTimeout(() => timeoutController.abort(), effectiveTimeout);

  // Combine external signal with timeout signal
  let combinedSignal: AbortSignal;
  if (externalSignal) {
    // Use AbortSignal.any to combine both signals
    combinedSignal = AbortSignal.any([timeoutController.signal, externalSignal]);
  } else {
    combinedSignal = timeoutController.signal;
  }

  try {
    // Use XMLHttpRequest for FormData with onUploadProgress, fetch for everything else
    let response: Response;

    if (onUploadProgress && body instanceof FormData) {
      response = await xhrUpload(fullUrl, body, headers, combinedSignal, onUploadProgress);
    } else {
      response = await fetch(fullUrl, {
        method,
        headers,
        body: fetchBody,
        credentials: 'include',
        signal: combinedSignal,
      });
    }

    clearTimeout(timeoutId);

    // Parse response body based on responseType or content-type
    let data: T;

    if (response.status === 204) {
      data = undefined as T;
    } else if (responseType === 'blob') {
      data = await response.blob() as T;
    } else if (responseType === 'text') {
      data = await response.text() as T;
    } else {
      // Default: try JSON, fall back to text
      const contentType = response.headers.get('content-type') || '';
      if (contentType.includes('application/json')) {
        data = await response.json() as T;
      } else {
        const text = await response.text();
        // Try parsing as JSON anyway (some servers don't set content-type correctly)
        try {
          data = JSON.parse(text) as T;
        } catch {
          data = text as T;
        }
      }
    }

    let result: ApiResponse<T> = { data, status: response.status, headers: response.headers };

    // Check for error status codes
    if (!response.ok) {
      const apiError = normalizeError(response.status, data);
      // On 401 from any authenticated call, redirect to /login with a next=
      // query param — unless the user is already on /login or /setup
      // (prevents redirect loops) or the request itself targets an auth
      // endpoint that legitimately returns 401 (bad creds / no session).
      maybeRedirectOnUnauthorized(response.status, config.url);
      throw new ApiClientError(apiError, { data, status: response.status }, config);
    }

    // Run response interceptors (onFulfilled)
    for (const interceptor of responseInterceptors) {
      if (interceptor.onFulfilled) {
        result = await interceptor.onFulfilled(result as ApiResponse, config) as ApiResponse<T>;
      }
    }

    return result;
  } catch (caughtError) {
    clearTimeout(timeoutId);

    // If it's already an ApiClientError, run error interceptors
    if (caughtError instanceof ApiClientError) {
      let currentError: ApiClientError = caughtError;
      for (const interceptor of responseInterceptors) {
        if (interceptor.onRejected) {
          try {
            const result = await interceptor.onRejected(currentError, config);
            // Interceptor handled the error and returned a valid response
            return result as ApiResponse<T>;
          } catch (interceptorError) {
            // Interceptor re-threw — continue to next interceptor or throw
            if (interceptorError instanceof ApiClientError) {
              currentError = interceptorError;
            }
          }
        }
      }
      throw currentError;
    }

    // Handle fetch-level errors (network, timeout, abort)
    if (caughtError instanceof DOMException || caughtError instanceof TypeError) {
      const isTimeout = caughtError.name === 'AbortError' && timeoutController.signal.aborted;
      const isExternalAbort = caughtError.name === 'AbortError' && externalSignal?.aborted;

      if (isExternalAbort) {
        // Re-throw abort errors from external signals as-is
        throw caughtError;
      }

      const apiError = normalizeError(
        null,
        undefined,
        isTimeout ? 'timeout' : caughtError.message,
      );

      const clientError = new ApiClientError(apiError, undefined, config);

      // Run error interceptors
      for (const interceptor of responseInterceptors) {
        if (interceptor.onRejected) {
          try {
            const result = await interceptor.onRejected(clientError, config);
            return result as ApiResponse<T>;
          } catch {
            // Interceptor re-threw — continue
          }
        }
      }

      throw clientError;
    }

    // Unknown error — re-throw as-is
    throw caughtError;
  }
}

/**
 * XMLHttpRequest-based upload for FormData with progress tracking.
 * Native fetch does not support upload progress events.
 */
function xhrUpload(
  url: string,
  formData: FormData,
  headers: Record<string, string>,
  signal: AbortSignal,
  onProgress: (event: { loaded: number; total?: number }) => void,
): Promise<Response> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.withCredentials = true;

    // Set headers (skip Content-Type — browser sets it with boundary for FormData)
    for (const [key, value] of Object.entries(headers)) {
      if (key.toLowerCase() !== 'content-type') {
        xhr.setRequestHeader(key, value);
      }
    }

    xhr.upload.addEventListener('progress', (event) => {
      onProgress({ loaded: event.loaded, total: event.lengthComputable ? event.total : undefined });
    });

    xhr.addEventListener('load', () => {
      // Build a Response-like object from XHR
      const responseHeaders = new Headers();
      const rawHeaders = xhr.getAllResponseHeaders().trim().split(/[\r\n]+/);
      for (const line of rawHeaders) {
        const idx = line.indexOf(':');
        if (idx > 0) {
          responseHeaders.append(line.substring(0, idx).trim(), line.substring(idx + 1).trim());
        }
      }

      const response = new Response(xhr.response, {
        status: xhr.status,
        statusText: xhr.statusText,
        headers: responseHeaders,
      });

      resolve(response);
    });

    xhr.addEventListener('error', () => {
      reject(new TypeError('Network error'));
    });

    xhr.addEventListener('abort', () => {
      reject(new DOMException('Aborted', 'AbortError'));
    });

    // Handle external abort signal
    if (signal.aborted) {
      reject(new DOMException('Aborted', 'AbortError'));
      return;
    }
    signal.addEventListener('abort', () => xhr.abort(), { once: true });

    xhr.send(formData);
  });
}

// ========================================
// Public API Client
// ========================================

/**
 * Fetch-based API client with axios-compatible interface.
 *
 * Supports `apiClient.get()`, `.post()`, `.put()`, `.patch()`, `.delete()`,
 * and can be called directly as `apiClient(config)` for request retries.
 */
/* eslint-disable @typescript-eslint/no-explicit-any -- Default any matches axios convention for drop-in compat */
export interface ApiClient {
  /** Execute a request directly from a config (used for retry in interceptors). */
  (config: InternalRequestConfig): Promise<ApiResponse>;
  get<T = any>(url: string, config?: RequestConfig): Promise<ApiResponse<T>>;
  post<T = any>(url: string, data?: unknown, config?: RequestConfig): Promise<ApiResponse<T>>;
  put<T = any>(url: string, data?: unknown, config?: RequestConfig): Promise<ApiResponse<T>>;
  patch<T = any>(url: string, data?: unknown, config?: RequestConfig): Promise<ApiResponse<T>>;
  delete<T = any>(url: string, config?: RequestConfig): Promise<ApiResponse<T>>;
  interceptors: {
    response: {
      use(onFulfilled?: ResponseInterceptor, onRejected?: ErrorInterceptor): number;
      eject(id: number): void;
    };
  };
  /** Base configuration (for compatibility with code that reads baseURL). */
  defaults: {
    baseURL: string;
  };
}
/* eslint-enable @typescript-eslint/no-explicit-any */

/** Execute a request from a full InternalRequestConfig (used for retries). */
function executeFromConfig(config: InternalRequestConfig): Promise<ApiResponse> {
  return executeRequest(config);
}

/** Create the public API client with method shortcuts. */
function createApiClient(): ApiClient {
  const client = function (config: InternalRequestConfig): Promise<ApiResponse> {
    return executeFromConfig(config);
  } as ApiClient;

  /* eslint-disable @typescript-eslint/no-explicit-any -- Implements ApiClient interface */
  client.get = <T = any>(url: string, config?: RequestConfig) =>
    executeRequest<T>({ ...config, url, method: 'GET' });

  client.post = <T = any>(url: string, data?: unknown, config?: RequestConfig) =>
    executeRequest<T>({ ...config, url, method: 'POST', body: data });

  client.put = <T = any>(url: string, data?: unknown, config?: RequestConfig) =>
    executeRequest<T>({ ...config, url, method: 'PUT', body: data });

  client.patch = <T = any>(url: string, data?: unknown, config?: RequestConfig) =>
    executeRequest<T>({ ...config, url, method: 'PATCH', body: data });

  client.delete = <T = any>(url: string, config?: RequestConfig) =>
    executeRequest<T>({ ...config, url, method: 'DELETE', body: config?.data });
  /* eslint-enable @typescript-eslint/no-explicit-any */

  client.defaults = { baseURL: API_BASE };

  client.interceptors = {
    response: {
      use(onFulfilled?: ResponseInterceptor, onRejected?: ErrorInterceptor): number {
        const id = nextInterceptorId++;
        responseInterceptors.push({ id, onFulfilled, onRejected });
        return id;
      },
      eject(id: number): void {
        const index = responseInterceptors.findIndex((i) => i.id === id);
        if (index !== -1) {
          responseInterceptors.splice(index, 1);
        }
      },
    },
  };

  return client;
}

export const apiClient = createApiClient();
