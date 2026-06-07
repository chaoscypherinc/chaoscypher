// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { Tooltip, Box, Typography, Stack, Dialog } from '@mui/material';
import SourceIcon from '@mui/icons-material/Description';
import ImageIcon from '@mui/icons-material/Image';
import type { ChunkCitationSummary } from '../../types';
import { CardColors, hexToRgba } from '../../theme/cardStyles';
import { ChatTheme } from '../../theme/chatTheme';
import { apiClient, API_BASE } from '../../services/api/client';

interface ChunkCitationProps {
  citation: ChunkCitationSummary;
}

/**
 * Tooltip content for chunk citation hover card.
 * Shows image preview when the citation references vision-processed content.
 */
function CitationTooltipContent({ citation, imageUrl }: { citation: ChunkCitationSummary; imageUrl?: string }) {
  const canNavigate = !!citation.source_id;

  return (
    <Stack spacing={0.5} sx={{ maxWidth: 350 }}>
      {/* Image preview for vision citations */}
      {imageUrl && (
        <Box
          component="img"
          src={imageUrl}
          alt={citation.label}
          sx={{
            maxWidth: '100%',
            maxHeight: 180,
            borderRadius: 0.5,
            objectFit: 'contain',
            mb: 0.5,
          }}
        />
      )}
      {/* Source header */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
        {citation.has_vision_image
          ? <ImageIcon sx={{ fontSize: '1rem', color: CardColors.info }} />
          : <SourceIcon sx={{ fontSize: '1rem', color: CardColors.info }} />
        }
        <Typography variant="subtitle2" sx={{
          fontWeight: 600
        }}>
          {citation.label}
        </Typography>
      </Box>
      {/* Page number (if available) */}
      {citation.page_number != null && (
        <Typography variant="caption" sx={{
          color: "text.secondary"
        }}>
          Page {citation.page_number}
        </Typography>
      )}
      {/* Validation status */}
      {citation.validation_verdict === 'correct' && (
        <Typography variant="caption" sx={{ color: 'success.main' }}>
          Verified — sentence reference valid
        </Typography>
      )}
      {citation.validation_verdict === 'wrong' && (
        <Typography variant="caption" sx={{ color: 'error.main' }}>
          Invalid — sentence reference not found in source
        </Typography>
      )}
      {/* Click hint */}
      {canNavigate && (
        <Typography
          variant="caption"
          sx={{
            color: "text.disabled",
            mt: 0.25
          }}>
          Click to view {citation.has_vision_image ? 'image' : 'in source'}
        </Typography>
      )}
    </Stack>
  );
}

/**
 * Inline chunk citation component.
 * For vision-processed chunks: shows a small image thumbnail inline.
 * For text chunks: shows a small document icon.
 * Both show details on hover and navigate to source on click.
 */
export default function ChunkCitation({ citation }: ChunkCitationProps) {
  const navigate = useNavigate();
  const [imageUrl, setImageUrl] = useState<string | undefined>();
  const [expandedImage, setExpandedImage] = useState(false);
  const iconColor = citation.validation_verdict === 'correct'
    ? ChatTheme.citation.verdictCorrect
    : citation.validation_verdict === 'wrong'
      ? ChatTheme.citation.verdictWrong
      : CardColors.info;
  const canNavigate = !!citation.source_id;

  // Fetch first image for vision citations
  useEffect(() => {
    if (!citation.has_vision_image || !citation.source_id) return;
    const fetchImage = async () => {
      try {
        const response = await apiClient.get<{ filename: string; url: string }[]>(
          `/sources/${citation.source_id}/images`
        );
        if (response.data.length > 0) {
          // Use page-specific image if page_number available, otherwise first
          const pageFile = citation.page_number
            ? response.data.find(img => img.filename === `page_${citation.page_number}.png`)
            : null;
          const img = pageFile || response.data[0];
          setImageUrl(`${API_BASE}${img.url}`);
        }
      } catch {
        // No images available
      }
    };
    fetchImage();
  }, [citation.has_vision_image, citation.source_id, citation.page_number]);

  const handleClick = useCallback((event: React.MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
    // For vision citations with an image, show expanded view
    if (citation.has_vision_image && imageUrl) {
      setExpandedImage(true);
      return;
    }
    // For text citations, navigate to source
    if (citation.source_id) {
      const params = new URLSearchParams();
      params.set('highlight', citation.chunk_id);
      navigate(`/sources/${citation.source_id}?${params.toString()}`);
    }
  }, [citation.source_id, citation.chunk_id, citation.has_vision_image, imageUrl, navigate]);

  // Vision citation: show inline thumbnail
  if (citation.has_vision_image && imageUrl) {
    return (
      <>
        <Tooltip
          title={<CitationTooltipContent citation={citation} imageUrl={imageUrl} />}
          arrow
          enterDelay={200}
          leaveDelay={100}
          placement="top"
          slotProps={{
            tooltip: {
              sx: {
                bgcolor: 'background.paper',
                color: 'text.primary',
                boxShadow: 3,
                border: `1px solid ${hexToRgba(CardColors.info, 0.3)}`,
                p: 1.5,
                '& .MuiTooltip-arrow': {
                  color: 'background.paper',
                  '&::before': {
                    border: `1px solid ${hexToRgba(CardColors.info, 0.3)}`,
                  },
                },
              },
            },
          }}
        >
          <Box
            component="img"
            src={imageUrl}
            alt={citation.label}
            onClick={handleClick}
            sx={{
              display: 'inline-block',
              height: 48,
              width: 64,
              objectFit: 'cover',
              borderRadius: 0.5,
              border: `1px solid ${hexToRgba(iconColor, 0.5)}`,
              verticalAlign: 'text-bottom',
              mx: 0.25,
              cursor: 'pointer',
              transition: 'all 0.2s ease-in-out',
              '&:hover': {
                opacity: 0.8,
                transform: 'translateY(-1px)',
              },
            }}
          />
        </Tooltip>
        <Dialog
          open={expandedImage}
          onClose={() => setExpandedImage(false)}
          maxWidth="lg"
        >
          <Box
            component="img"
            src={imageUrl}
            alt={citation.label}
            sx={{ maxWidth: '100%', maxHeight: '85vh', display: 'block' }}
            onClick={() => setExpandedImage(false)}
          />
        </Dialog>
      </>
    );
  }

  // Text citation: show document icon (original behavior)
  return (
    <Tooltip
      title={<CitationTooltipContent citation={citation} />}
      arrow
      enterDelay={200}
      leaveDelay={100}
      placement="top"
      slotProps={{
        tooltip: {
          sx: {
            bgcolor: 'background.paper',
            color: 'text.primary',
            boxShadow: 3,
            border: `1px solid ${hexToRgba(CardColors.info, 0.3)}`,
            p: 1.5,
            '& .MuiTooltip-arrow': {
              color: 'background.paper',
              '&::before': {
                border: `1px solid ${hexToRgba(CardColors.info, 0.3)}`,
              },
            },
          },
        },
      }}
    >
      <SourceIcon
        component="svg"
        onClick={canNavigate ? handleClick : undefined}
        sx={{
          fontSize: '1rem',
          verticalAlign: 'text-bottom',
          mx: 0.25,
          color: iconColor,
          cursor: canNavigate ? 'pointer' : 'default',
          transition: 'all 0.2s ease-in-out',
          '&:hover': {
            opacity: 0.8,
            ...(canNavigate && { transform: 'translateY(-1px)' }),
          },
        }}
      />
    </Tooltip>
  );
}
