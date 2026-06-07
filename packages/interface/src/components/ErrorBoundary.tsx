// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ErrorBoundary Component
 *
 * Class component that catches JavaScript errors in its child component tree,
 * logs the error, and renders a fallback UI instead of crashing the whole app.
 * Supports custom fallback UI via props and an error callback for reporting.
 */

import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { Box, Typography, Button, Paper, Collapse } from '@mui/material';
import { Overlays } from '../theme/overlays';
import { ghostButtonSx } from '../theme/ghostStyles';
import { ChaosCypherPalette } from '../theme/palette';
import ErrorOutlinedIcon from '@mui/icons-material/ErrorOutlined';
import { logger } from '../utils/logger';

/** Props for the ErrorBoundary component */
interface ErrorBoundaryProps {
  /** Child components to render when no error is present */
  children: ReactNode;
  /** Optional custom fallback UI to display when an error occurs */
  fallback?: ReactNode;
  /** Optional callback invoked when an error is caught */
  onError?: (error: Error, errorInfo: ErrorInfo) => void;
}

/** Internal state for the ErrorBoundary component */
interface ErrorBoundaryState {
  /** Whether an error has been caught */
  hasError: boolean;
  /** The caught error, if any */
  error: Error | null;
  /**
   * Short, opaque identifier generated when an error is caught. Surfaced
   * in the fallback UI so a user reporting a bug can quote a stable
   * reference and we can grep it out of the console log. Has no security
   * implication — it's a random tag, not a session token.
   */
  errorId: string | null;
  /** Whether the raw error message is expanded in the fallback UI. */
  showDetails: boolean;
}

/**
 * Error boundary that catches render errors in its child tree
 *
 * Displays a fallback UI with error details and a retry button.
 * Accepts an optional custom fallback or onError callback for
 * external error reporting.
 *
 * @example
 * ```tsx
 * <ErrorBoundary onError={(err) => reportError(err)}>
 *   <MyPage />
 * </ErrorBoundary>
 *
 * // With custom fallback:
 * <ErrorBoundary fallback={<div>Something went wrong</div>}>
 *   <MyPage />
 * </ErrorBoundary>
 * ```
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null, errorId: null, showDetails: false };
  }

  static getDerivedStateFromError(error: Error): Partial<ErrorBoundaryState> {
    // Short, opaque ID — Date in base-36 + a short random tail. Stable
    // until the boundary re-mounts so the user can quote it in a report.
    const errorId =
      'err-' +
      Date.now().toString(36) +
      '-' +
      Math.random().toString(36).slice(2, 8);
    return { hasError: true, error, errorId, showDetails: false };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    logger.error('[ErrorBoundary] Caught error:', error, errorInfo, {
      errorId: this.state.errorId,
    });
    this.props.onError?.(error, errorInfo);
  }

  toggleDetails = (): void => {
    this.setState((prev) => ({ showDetails: !prev.showDetails }));
  };

  /**
   * Force a full browser reload. A soft state reset can't recover from
   * failed lazy-loaded chunk imports (e.g., when the backend restarts
   * mid-navigation) because React.lazy caches rejected promises — only
   * a real page reload clears that cache.
   */
  reloadPage = (): void => {
    window.location.reload();
  };

  render(): ReactNode {
    if (!this.state.hasError) {
      return this.props.children;
    }

    if (this.props.fallback) {
      return this.props.fallback;
    }

    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          minHeight: "400px",
          p: 3
        }}>
        <Paper
          sx={{
            p: 4,
            maxWidth: 520,
            width: '100%',
            textAlign: 'center',
            backgroundColor: (theme) =>
              theme.palette.mode === 'dark'
                ? Overlays.subtle.dark
                : Overlays.subtle.light,
          }}
        >
          <ErrorOutlinedIcon
            sx={{ fontSize: 48, color: 'error.main', mb: 2 }}
          />

          <Typography variant="h6" gutterBottom>
            Something went wrong
          </Typography>

          <Typography variant="body2" sx={{ color: 'text.secondary', mb: 1 }}>
            Try reloading the page. If the problem persists, please report
            this with the reference below.
          </Typography>

          {this.state.errorId && (
            <Typography
              variant="caption"
              sx={{ display: 'block', color: 'text.secondary', mb: 2, fontFamily: 'monospace' }}
            >
              Reference: {this.state.errorId}
            </Typography>
          )}

          <Collapse in={this.state.showDetails} unmountOnExit>
            <Typography
              variant="body2"
              data-testid="error-boundary-details"
              sx={{
                color: 'text.secondary',
                mb: 2,
                wordBreak: 'break-word',
                fontFamily: 'monospace',
                fontSize: '0.8rem',
                textAlign: 'left',
                backgroundColor: (theme) =>
                  theme.palette.mode === 'dark'
                    ? Overlays.light.dark
                    : Overlays.light.light,
                borderRadius: 1,
                p: 1.5,
              }}
            >
              {this.state.error?.message || 'An unexpected error occurred.'}
            </Typography>
          </Collapse>

          <Box sx={{ display: 'flex', flexDirection: 'row', gap: 1, justifyContent: 'center' }}>
            <Button
              variant="text"
              size="small"
              onClick={this.toggleDetails}
              aria-expanded={this.state.showDetails}
            >
              {this.state.showDetails ? 'Hide details' : 'Show details'}
            </Button>
            <Button
              variant="outlined"
              onClick={this.reloadPage}
              sx={ghostButtonSx(ChaosCypherPalette.primary)}
            >
              Try Again
            </Button>
          </Box>
        </Paper>
      </Box>
    );
  }
}
