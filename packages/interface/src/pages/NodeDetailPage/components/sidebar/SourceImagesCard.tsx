// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, Dialog, Typography } from '@mui/material';
import { glassPanelSx } from '../../../../theme/cardStyles';
import { API_BASE } from '../../../../services/api/client';

interface SourceImagesCardProps {
  images: { filename: string; url: string }[];
  expandedImage: string | null;
  onExpand: (url: string | null) => void;
}

/**
 * Sidebar card displaying source-document image thumbnails for a node with
 * an expanded-image dialog. Renders nothing when `images` is empty.
 */
export default function SourceImagesCard({
  images,
  expandedImage,
  onExpand,
}: SourceImagesCardProps) {
  if (images.length === 0) return null;

  return (
    <>
      <Box sx={{ ...glassPanelSx, p: 2.5, mt: 2 }}>
        <Typography variant="h6" gutterBottom sx={{ color: 'text.primary' }}>
          Source Image
        </Typography>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          {images.map((img) => (
            <Box
              key={img.filename}
              component="img"
              src={`${API_BASE}${img.url}`}
              alt={img.filename}
              onClick={() => onExpand(`${API_BASE}${img.url}`)}
              sx={{
                width: '100%',
                borderRadius: 1,
                border: '1px solid rgba(255, 255, 255, 0.06)',
                cursor: 'pointer',
                transition: 'opacity 0.15s',
                '&:hover': { opacity: 0.85 },
              }}
            />
          ))}
        </Box>
      </Box>

      <Dialog
        open={!!expandedImage}
        onClose={() => onExpand(null)}
        maxWidth="lg"
        slotProps={{
          paper: {
            sx: {
              bgcolor: 'rgba(10, 14, 23, 0.95)',
              border: '1px solid rgba(255, 255, 255, 0.06)',
              borderRadius: '12px',
            },
          },
        }}
      >
        {expandedImage && (
          <Box
            component="img"
            src={expandedImage}
            alt="Source image"
            sx={{ maxWidth: '100%', maxHeight: '85vh', display: 'block' }}
            onClick={() => onExpand(null)}
          />
        )}
      </Dialog>
    </>
  );
}
