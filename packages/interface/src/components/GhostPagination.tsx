// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Ghost-styled pagination control for data tables.
 *
 * Terminal-aesthetic pagination with chevron nav, glowing active page,
 * and a "Go to page" jump input for power users.
 */

import { useState } from 'react';
import {
  Box,
  Typography,
  IconButton,
  TextField,
} from '@mui/material';
import PrevIcon from '@mui/icons-material/ChevronLeft';
import NextIcon from '@mui/icons-material/ChevronRight';

interface GhostPaginationProps {
  page: number;
  totalPages: number;
  total: number;
  pageSize: number;
  onPageChange: (page: number) => void;
}

/** How many page numbers to show around the current page. */
const SIBLING_COUNT = 2;

function getPageNumbers(current: number, totalPages: number): (number | '...')[] {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, i) => i + 1);
  }

  const pages: (number | '...')[] = [];
  const left = Math.max(2, current - SIBLING_COUNT);
  const right = Math.min(totalPages - 1, current + SIBLING_COUNT);

  pages.push(1);
  if (left > 2) pages.push('...');
  for (let i = left; i <= right; i++) pages.push(i);
  if (right < totalPages - 1) pages.push('...');
  if (totalPages > 1) pages.push(totalPages);

  return pages;
}

export default function GhostPagination({
  page,
  totalPages,
  total,
  pageSize,
  onPageChange,
}: GhostPaginationProps) {
  const [jumpInput, setJumpInput] = useState('');

  if (totalPages <= 1) return null;

  const start = (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, total);
  const pageNumbers = getPageNumbers(page, totalPages);

  const handleJump = () => {
    const target = parseInt(jumpInput, 10);
    if (target >= 1 && target <= totalPages && target !== page) {
      onPageChange(target);
    }
    setJumpInput('');
  };

  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 1,
        py: 2,
      }}
    >
      {/* Item count */}
      <Typography
        variant="caption"
        sx={{ color: 'rgba(255, 255, 255, 0.35)', fontFamily: 'monospace', fontSize: '0.7rem', mr: 2 }}
      >
        {start}–{end} of {total.toLocaleString()}
      </Typography>
      {/* Prev */}
      <IconButton
        aria-label="Previous page"
        size="small"
        disabled={page <= 1}
        onClick={() => onPageChange(page - 1)}
        sx={{
          color: page > 1 ? 'primary.main' : 'text.disabled',
          '&:hover': { bgcolor: 'rgba(255, 255, 255, 0.05)' },
        }}
      >
        <PrevIcon fontSize="small" />
      </IconButton>
      {/* Page numbers */}
      {pageNumbers.map((p, i) =>
        p === '...' ? (
          <Typography
            key={`dots-${i}`}
            variant="caption"
            sx={{ color: 'rgba(255, 255, 255, 0.3)', px: 0.5, userSelect: 'none' }}
          >
            ...
          </Typography>
        ) : (
          <Typography
            key={p}
            variant="caption"
            onClick={() => p !== page && onPageChange(p)}
            sx={{
              px: 1,
              py: 0.25,
              cursor: p === page ? 'default' : 'pointer',
              fontFamily: 'monospace',
              fontSize: '0.8rem',
              fontWeight: p === page ? 700 : 400,
              color: p === page ? 'primary.main' : 'rgba(255, 255, 255, 0.4)',
              borderBottom: p === page ? '1px solid' : '1px solid transparent',
              borderColor: p === page ? 'primary.main' : 'transparent',
              transition: 'color 0.15s, border-color 0.15s',
              '&:hover': p !== page ? { color: 'rgba(255, 255, 255, 0.7)' } : {},
            }}
          >
            {p}
          </Typography>
        ),
      )}
      {/* Next */}
      <IconButton
        aria-label="Next page"
        size="small"
        disabled={page >= totalPages}
        onClick={() => onPageChange(page + 1)}
        sx={{
          color: page < totalPages ? 'primary.main' : 'text.disabled',
          '&:hover': { bgcolor: 'rgba(255, 255, 255, 0.05)' },
        }}
      >
        <NextIcon fontSize="small" />
      </IconButton>
      {/* Jump input */}
      {totalPages > 7 && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, ml: 2 }}>
          <Typography
            variant="caption"
            sx={{ color: 'rgba(255, 255, 255, 0.3)', fontSize: '0.65rem', whiteSpace: 'nowrap' }}
          >
            Go to
          </Typography>
          <TextField
            size="small"
            variant="standard"
            value={jumpInput}
            onChange={(e) => setJumpInput(e.target.value.replace(/\D/g, ''))}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleJump();
            }}
            onBlur={handleJump}
            placeholder="#"
            sx={{
              width: 40,
              '& .MuiInputBase-root': {
                backgroundColor: 'transparent',
                '&:before, &:after': { borderColor: 'rgba(255, 255, 255, 0.1)' },
              },
              '& .MuiInputBase-input': {
                textAlign: 'center',
                fontFamily: 'monospace',
                fontSize: '0.75rem',
                py: 0.25,
                color: 'primary.main',
              },
            }}
          />
        </Box>
      )}
    </Box>
  );
}
