// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ModelOptionItem: Renders a single model entry within the embedding model
 * autocomplete dropdown.
 *
 * Displays model name, description, installed status icon, and provider-specific
 * action buttons (download, delete, context menu). Includes an inline download
 * progress bar for Ollama pulls.
 */

import React from 'react';
import {
  Box,
  Typography,
  Chip,
  CircularProgress,
  IconButton,
  Tooltip,
  LinearProgress,
} from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import DownloadIcon from '@mui/icons-material/Download';
import DeleteOutlinedIcon from '@mui/icons-material/DeleteOutlined';
import MoreVertIcon from '@mui/icons-material/MoreVert';
import type { EmbeddingOption } from '../hooks/useEmbeddingModels';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PullProgressInfo {
  status: string;
  completed: number;
  total: number;
}

interface ModelOptionItemProps {
  /** HTML props forwarded from MUI Autocomplete renderOption. */
  htmlProps: React.HTMLAttributes<HTMLLIElement>;
  /** The embedding model option to render. */
  option: EmbeddingOption;
  /** The currently active model ID. */
  activeModelId: string;
  /** Provider context: 'local' | 'ollama'. */
  provider: 'local' | 'ollama';
  /** Whether this model is currently being downloaded (local provider only). */
  isDownloading: boolean;
  /** Pull progress info for Ollama models, if a pull is active. */
  pullProgress?: PullProgressInfo;
  /** Callback to open the 3-dot context menu (Ollama only). */
  onMenuOpen: (event: React.MouseEvent<HTMLElement>, modelId: string) => void;
  /** Callback to start an Ollama model pull. */
  onPullModel: (modelId: string) => void;
  /** Callback to download a local embedding model. */
  onLocalDownload: (modelId: string) => void;
  /** Callback to delete a local embedding model. */
  onLocalDelete: (modelId: string, event: React.MouseEvent) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export const ModelOptionItem = React.memo(function ModelOptionItem({
  htmlProps,
  option,
  activeModelId,
  provider,
  isDownloading,
  pullProgress,
  onMenuOpen,
  onPullModel,
  onLocalDownload,
  onLocalDelete,
}: ModelOptionItemProps) {
  const isOllama = provider === 'ollama';
  const isLocal = provider === 'local';
  const isPulling = isOllama && pullProgress !== undefined;

  return (
    <Box
      component="li"
      {...htmlProps}
      key={option.id}
      sx={{ display: 'flex', flexDirection: 'column', alignItems: 'stretch !important', py: 0.5 }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, width: '100%' }}>
        {/* Left icon: installed status */}
        {option.installed
          ? <CheckCircleIcon color="success" sx={{ fontSize: 18, flexShrink: 0 }} />
          : <DownloadIcon sx={{ fontSize: 18, flexShrink: 0, color: 'text.disabled' }} />
        }

        {/* Center: model info */}
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="body2" noWrap sx={{ fontWeight: 'medium' }}>
            {option.name}
          </Typography>
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
            {option.id}{option.description ? ` · ${option.description}` : ''}
          </Typography>
        </Box>

        {option.id === activeModelId && (
          <Chip size="small" label="Active" color="primary" sx={{ height: 18, fontSize: '0.65rem' }} />
        )}

        {/* Right action: 3-dot menu (installed) or download (not installed) */}
        {isOllama && option.installed && (
          <IconButton
            aria-label="More actions"
            size="small"
            onMouseDown={(e) => { e.stopPropagation(); e.preventDefault(); }}
            onClick={(e) => { e.stopPropagation(); e.preventDefault(); onMenuOpen(e, option.id); }}
            sx={{ flexShrink: 0 }}
          >
            <MoreVertIcon fontSize="small" />
          </IconButton>
        )}
        {isOllama && !option.installed && !isPulling && (
          <Tooltip title={`Download ${option.name}`}>
            <IconButton
              aria-label={`Download ${option.name}`}
              size="small"
              onMouseDown={(e) => { e.stopPropagation(); e.preventDefault(); }}
              onClick={(e) => { e.stopPropagation(); e.preventDefault(); onPullModel(option.id); }}
              sx={{ flexShrink: 0 }}
            >
              <DownloadIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}
        {isLocal && !option.installed && (
          isDownloading
            ? <CircularProgress size={18} sx={{ flexShrink: 0 }} />
            : (
              <Tooltip title="Download model">
                <IconButton
                  aria-label="Download model"
                  size="small"
                  onClick={(e) => { e.stopPropagation(); onLocalDownload(option.id); }}
                  sx={{ p: 0 }}
                >
                  <DownloadIcon sx={{ fontSize: 18, color: 'text.disabled' }} />
                </IconButton>
              </Tooltip>
            )
        )}
        {isLocal && option.installed && option.id !== activeModelId && (
          <Tooltip title="Delete model">
            <IconButton
              aria-label="Delete model"
              size="small"
              onClick={(e) => onLocalDelete(option.id, e)}
              sx={{ p: 0, ml: 0.5 }}
            >
              <DeleteOutlinedIcon sx={{ fontSize: 16, color: 'text.disabled' }} />
            </IconButton>
          </Tooltip>
        )}
      </Box>

      {/* Ollama pull progress bar */}
      {isPulling && pullProgress && (
        <Box sx={{ width: '100%', mt: 0.5 }}>
          <LinearProgress
            variant={pullProgress.total > 0 ? 'determinate' : 'indeterminate'}
            value={pullProgress.total > 0 ? (pullProgress.completed / pullProgress.total) * 100 : 0}
            sx={{ height: 3, borderRadius: 1 }}
          />
          <Typography
            variant="caption"
            sx={{ color: 'text.secondary', fontSize: '0.65rem' }}
          >
            {pullProgress.status}
            {pullProgress.total > 0 && ` (${Math.round((pullProgress.completed / pullProgress.total) * 100)}%)`}
          </Typography>
        </Box>
      )}
    </Box>
  );
});
