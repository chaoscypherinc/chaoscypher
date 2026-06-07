// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * DeviceAuthDialog — Glassmorphic dialog for Lexicon device authentication flow.
 */
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Box,
  Typography,
  CircularProgress,
  IconButton,
  Tooltip,
  Link,
  alpha,
} from '@mui/material';
import ContentCopy from '@mui/icons-material/ContentCopy';
import OpenInNew from '@mui/icons-material/OpenInNew';
import { useEffect, useRef, useState } from 'react';
import type { LexiconDeviceCodeResponse } from '../../types/lexicon';
import { ChaosCypherPalette } from '../../theme/palette';
import {
  ghostButtonSx,
  ghostCancelBtnSx,
  ghostDialogPaperSx,
} from '../../theme/ghostStyles';

interface DeviceAuthDialogProps {
  open: boolean;
  deviceCode: LexiconDeviceCodeResponse | null;
  onClose: () => void;
}

export function DeviceAuthDialog({
  open,
  deviceCode,
  onClose,
}: DeviceAuthDialogProps) {
  const [copied, setCopied] = useState(false);
  const copyResetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (copyResetTimer.current) clearTimeout(copyResetTimer.current);
    };
  }, []);

  const handleCopy = () => {
    if (deviceCode?.user_code) {
      navigator.clipboard.writeText(deviceCode.user_code);
      setCopied(true);
      if (copyResetTimer.current) clearTimeout(copyResetTimer.current);
      copyResetTimer.current = setTimeout(() => setCopied(false), 2000);
    }
  };

  const handleOpenLink = () => {
    if (deviceCode?.verification_uri_complete) {
      window.open(deviceCode.verification_uri_complete, '_blank', 'noopener,noreferrer');
    } else if (deviceCode?.verification_uri) {
      window.open(deviceCode.verification_uri, '_blank', 'noopener,noreferrer');
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      slotProps={{ paper: { sx: ghostDialogPaperSx } }}
    >
      <DialogTitle sx={{ color: 'text.primary' }}>Login to Lexicon</DialogTitle>
      <DialogContent>
        {deviceCode ? (
          <Box sx={{ textAlign: 'center', py: 2 }}>
            <Typography sx={{ color: 'text.secondary' }} gutterBottom>
              To authenticate, visit the following URL:
            </Typography>

            <Box sx={{ my: 2 }}>
              <Link
                href={deviceCode.verification_uri_complete || deviceCode.verification_uri}
                target="_blank"
                rel="noopener noreferrer"
                sx={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 0.5,
                  fontSize: '1rem',
                  color: 'primary.main',
                }}
              >
                {deviceCode.verification_uri}
                <OpenInNew fontSize="small" />
              </Link>
            </Box>

            <Typography sx={{ color: 'text.disabled' }} gutterBottom>
              And enter this code:
            </Typography>

            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 1,
                my: 2,
              }}
            >
              <Typography
                variant="h4"
                component="code"
                sx={{
                  fontFamily: 'monospace',
                  fontWeight: 'bold',
                  letterSpacing: 4,
                  color: 'primary.main',
                  bgcolor: alpha(ChaosCypherPalette.primary, 0.06),
                  border: `1px solid ${alpha(ChaosCypherPalette.primary, 0.2)}`,
                  px: 3,
                  py: 1,
                  borderRadius: '8px',
                }}
              >
                {deviceCode.user_code}
              </Typography>
              <Tooltip title={copied ? 'Copied!' : 'Copy code'}>
                <IconButton
                  aria-label={copied ? 'Copied!' : 'Copy code'}
                  onClick={handleCopy}
                  size="small"
                  sx={{ color: 'text.disabled', '&:hover': { color: 'primary.main' } }}
                >
                  <ContentCopy />
                </IconButton>
              </Tooltip>
            </Box>

            <Box sx={{ display: 'flex', justifyContent: 'center', gap: 2, my: 3 }}>
              <Button
                variant="outlined"
                onClick={handleOpenLink}
                startIcon={<OpenInNew />}
                sx={ghostButtonSx(ChaosCypherPalette.primary)}
              >
                Open in Browser
              </Button>
            </Box>

            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 1, mt: 3 }}>
              <CircularProgress size={20} sx={{ color: 'primary.main' }} />
              <Typography sx={{ color: 'text.disabled', fontSize: 14 }}>
                Waiting for authorization...
              </Typography>
            </Box>

            <Typography sx={{ display: 'block', mt: 2, color: 'text.secondary', fontSize: 12 }}>
              This code expires in {Math.floor(deviceCode.expires_in / 60)} minutes
            </Typography>
          </Box>
        ) : (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
            <CircularProgress sx={{ color: 'primary.main' }} />
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} sx={ghostCancelBtnSx}>
          Cancel
        </Button>
      </DialogActions>
    </Dialog>
  );
}
