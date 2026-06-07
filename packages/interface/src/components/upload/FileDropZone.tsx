// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * @module FileDropZone
 * Drag-and-drop file upload area with URL input for the upload dialog.
 */

import { useCallback, useState } from 'react';
import {
  Box,
  Typography,
  TextField,
  Divider,
  alpha,
} from '@mui/material';
import CloudUploadIcon from '@mui/icons-material/CloudUpload';
import { ChaosCypherPalette, ChaosCypherNeutrals } from '../../theme/palette';
import { ghostInputSx } from '../../theme/ghostStyles';
import { useAppConfig } from '../../contexts/useAppConfig';

/** Format the upload cap byte count as a human-friendly string for the hint line. */
function formatCap(bytes: number): string {
  if (bytes >= 1024 ** 3) {
    const gb = bytes / 1024 ** 3;
    return `${gb % 1 === 0 ? gb.toFixed(0) : gb.toFixed(1)} GB`;
  }
  if (bytes >= 1024 ** 2) return `${Math.round(bytes / 1024 ** 2)} MB`;
  return `${bytes} B`;
}

interface FileDropZoneProps {
  hasFiles: boolean;
  hasUrl: boolean;
  urlInput: string;
  urlError: string;
  importingUrl: boolean;
  onFilesSelected: (files: File[]) => void;
  onUrlInputChange: (value: string) => void;
  onUrlErrorClear: () => void;
  onUrlSubmit: () => void;
  /** Opens the native file picker (input lives outside the Dialog) */
  onBrowseClick: () => void;
}

export function FileDropZone({
  hasFiles,
  hasUrl,
  urlInput,
  urlError,
  importingUrl,
  onFilesSelected,
  onUrlInputChange,
  onUrlErrorClear,
  onUrlSubmit,
  onBrowseClick,
}: FileDropZoneProps) {
  const [isDragOver, setIsDragOver] = useState(false);
  const { batch_max_upload_bytes: maxUploadBytes } = useAppConfig();
  const uploadCapLabel = formatCap(maxUploadBytes);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragOver(false);
      const files = e.dataTransfer.files;
      if (files.length > 0) {
        onFilesSelected(Array.from(files));
      }
    },
    [onFilesSelected]
  );

  return (
    <>
      {/* URL Input */}
      {!hasFiles && (
        <>
          <TextField
            fullWidth
            label="URL"
            placeholder="https://example.com/article"
            sx={{ mt: 1, ...ghostInputSx }}
            value={urlInput}
            onChange={(e) => {
              onUrlInputChange(e.target.value);
              onUrlErrorClear();
            }}
            error={!!urlError}
            helperText={urlError || 'Import content from a web page'}
            size="small"
            disabled={importingUrl}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && urlInput.trim()) {
                onUrlSubmit();
              }
            }}
          />

          <Divider sx={{ borderColor: 'rgba(255, 255, 255, 0.06)' }}>
            <Typography variant="caption" sx={{ color: ChaosCypherNeutrals.textMuted }}>or</Typography>
          </Divider>
        </>
      )}

      {/* File Upload Area (only when no files selected) */}
      {!hasFiles && (
        <Box
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onClick={onBrowseClick}
          sx={{
            border: '2px dashed',
            borderColor: isDragOver ? ChaosCypherPalette.primary : 'rgba(255, 255, 255, 0.1)',
            borderRadius: 2,
            p: 3,
            textAlign: 'center',
            cursor: hasUrl ? 'default' : 'pointer',
            bgcolor: isDragOver ? 'rgba(0, 229, 255, 0.04)' : 'transparent',
            opacity: hasUrl ? 0.5 : 1,
            pointerEvents: hasUrl ? 'none' : 'auto',
            transition: 'all 0.2s ease',
            '&:hover': hasUrl ? {} : {
              borderColor: alpha(ChaosCypherPalette.primary, 0.4),
              bgcolor: 'rgba(0, 229, 255, 0.04)',
            },
          }}
        >
          <CloudUploadIcon
            sx={{
              fontSize: 40,
              color: isDragOver ? ChaosCypherPalette.primary : ChaosCypherNeutrals.textMuted,
              mb: 0.5,
            }}
          />
          <Typography
            variant="body2"
            gutterBottom
            sx={{
              fontWeight: "medium",
              color: 'text.primary'
            }}>
            Drop files here or click to browse
          </Typography>
          <Typography variant="caption" component="span" sx={{ color: 'text.disabled' }}>
            <Box component="span" sx={{
              fontWeight: "medium"
            }}>Documents:</Box> PDF, DOCX, TXT, HTML
            {' \u00B7 '}
            <Box component="span" sx={{
              fontWeight: "medium"
            }}>Data:</Box> CSV, JSON
            {' \u00B7 '}
            <Box component="span" sx={{
              fontWeight: "medium"
            }}>Media:</Box> Images, Audio, Video
            {' \u00B7 '}
            Max {uploadCapLabel} per file
          </Typography>
        </Box>
      )}
    </>
  );
}
