// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Ollama URL Field Component
 *
 * Reusable component for Ollama URL input with verification.
 * Includes a text field with verify button and verification status alert.
 */
import {
  Box,
  TextField,
  InputAdornment,
  Button,
  CircularProgress,
  Alert,
  Typography,
  Chip,
} from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import ErrorIcon from '@mui/icons-material/Error';
import type { OllamaVerifyResponse } from '../../types';

/**
 * Get button color for URL verification state.
 */
function getVerificationColor(success: boolean | undefined): 'success' | 'error' | 'inherit' {
  if (success === true) return 'success';
  if (success === false) return 'error';
  return 'inherit';
}

interface OllamaUrlFieldProps {
  /** Current Ollama URL value */
  url: string;
  /** Callback when URL changes */
  onChange: (url: string) => void;
  /** Current verification result (null if not verified) */
  verification: OllamaVerifyResponse | null;
  /** Callback to trigger verification */
  onVerify: () => void;
  /** Whether verification is in progress */
  verifying: boolean;
  /** Callback to clear verification status */
  onClearVerification?: () => void;
}

/**
 * Ollama URL input field with verification button and status display.
 */
export default function OllamaUrlField({
  url,
  onChange,
  verification,
  onVerify,
  verifying,
  onClearVerification,
}: OllamaUrlFieldProps) {
  return (
    <Box>
      <TextField
        label="Ollama URL"
        variant="outlined"
        value={url}
        onChange={(e) => onChange(e.target.value)}
        fullWidth
        helperText="For Docker: http://host.docker.internal:11434 (or http://localhost:11434 if running locally)"
        slotProps={{
          input: {
            endAdornment: (
              <InputAdornment position="end">
                <Button
                  onClick={onVerify}
                  disabled={verifying || !url}
                  size="small"
                  color={getVerificationColor(verification?.success)}
                  startIcon={
                    verifying ? (
                      <CircularProgress size={16} />
                    ) : verification?.success ? (
                      <CheckCircleIcon />
                    ) : verification?.success === false ? (
                      <ErrorIcon />
                    ) : null
                  }
                  sx={{ whiteSpace: 'nowrap' }}
                >
                  {verifying ? 'Testing...' : 'Test Connection'}
                </Button>
              </InputAdornment>
            ),
          }
        }}
      />
      {/* Verification Status */}
      {verification && (
        <Alert
          severity={verification.success ? 'success' : 'error'}
          sx={{ mt: 1 }}
          onClose={onClearVerification}
        >
          <Box>
            <Typography variant="body2">{verification.message}</Typography>
            {verification.success && (
              <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mt: 0.5 }}>
                {verification.version && (
                  <Chip size="small" label={`v${verification.version}`} variant="outlined" />
                )}
                {verification.model_count !== null && verification.model_count !== undefined && (
                  <Chip size="small" label={`${verification.model_count} models`} variant="outlined" />
                )}
                {verification.response_time_ms && (
                  <Chip size="small" label={`${verification.response_time_ms}ms`} variant="outlined" />
                )}
              </Box>
            )}
          </Box>
        </Alert>
      )}
    </Box>
  );
}
