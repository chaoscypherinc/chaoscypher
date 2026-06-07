// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * @module FileList
 * Displays selected files with size, type, and remove/clear actions.
 */

import {
  Box,
  Typography,
  IconButton,
  Tooltip,
} from '@mui/material';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import CloseIcon from '@mui/icons-material/Close';
import AddIcon from '@mui/icons-material/Add';
import DeleteSweepIcon from '@mui/icons-material/DeleteSweep';
import { AccentSection } from '../AccentSection';
import { ChaosCypherPalette } from '../../theme/palette';

interface FileListProps {
  selectedFiles: File[];
  onRemoveFile: (index: number) => void;
  onClearSelection: () => void;
  onBrowseClick: () => void;
}

export function FileList({
  selectedFiles,
  onRemoveFile,
  onClearSelection,
  onBrowseClick,
}: FileListProps) {
  if (selectedFiles.length === 0) return null;

  return (
    <>
      <AccentSection
        color="file"
        sx={{
          maxHeight: 180,
          overflow: 'auto',
        }}
      >
        {selectedFiles.map((file, idx) => {
          const ext = file.name.split('.').pop()?.toUpperCase() || '';
          const sizeStr = file.size < 1024 * 1024
            ? `${(file.size / 1024).toFixed(0)} KB`
            : `${(file.size / 1024 / 1024).toFixed(1)} MB`;
          return (
            <Box
              key={file.name}
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1.5,
                py: 1,
                borderBottom: idx < selectedFiles.length - 1 ? '1px solid' : 'none',
                borderColor: 'rgba(255, 255, 255, 0.06)'
              }}>
              <Box sx={{
                bgcolor: 'rgba(0, 229, 255, 0.08)',
                borderRadius: 1.5,
                p: 1,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}>
                <UploadFileIcon sx={{ fontSize: 22, color: ChaosCypherPalette.primary }} />
              </Box>
              <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                <Typography
                  variant="body2"
                  noWrap
                  sx={{
                    fontWeight: 600,
                    color: 'text.primary'
                  }}>
                  {file.name}
                </Typography>
                <Typography variant="caption" sx={{ color: 'text.disabled' }}>
                  {sizeStr} {ext && `· ${ext} Document`}
                </Typography>
              </Box>
              <IconButton
                aria-label="Remove file"
                size="small"
                onClick={() => onRemoveFile(idx)}
                sx={{ p: 0.25, color: 'text.disabled', '&:hover': { color: 'error.main' } }}
              >
                <CloseIcon fontSize="small" />
              </IconButton>
            </Box>
          );
        })}
      </AccentSection>

      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center"
        }}>
        <Tooltip title="Add more files" arrow>
          <IconButton
            aria-label="Add more files"
            size="small"
            onClick={onBrowseClick}
            sx={{ color: ChaosCypherPalette.primary, '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.08)' } }}
          >
            <AddIcon />
          </IconButton>
        </Tooltip>
        <Tooltip title="Clear all files" arrow>
          <IconButton
            aria-label="Clear all files"
            size="small"
            onClick={onClearSelection}
            sx={{ color: 'text.disabled', '&:hover': { color: 'error.main', bgcolor: 'rgba(255, 0, 60, 0.08)' } }}
          >
            <DeleteSweepIcon />
          </IconButton>
        </Tooltip>
      </Box>
    </>
  );
}
