// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * PackageCard — Glassmorphic card for displaying a Lexicon package.
 */
import {
  Card,
  CardContent,
  CardActions,
  Typography,
  Chip,
  Box,
  Button,
  CircularProgress,
} from '@mui/material';
import { formatCompactNumber } from '../../utils/formatters';
import Download from '@mui/icons-material/Download';
import Person from '@mui/icons-material/Person';
import Update from '@mui/icons-material/Update';
import Star from '@mui/icons-material/Star';
import type { LexiconPackageInfo } from '../../types/lexicon';
import { ghostButtonSx } from '../../theme/ghostStyles';
import { ChaosCypherPalette, ChaosCypherNeutrals } from '../../theme/palette';

interface PackageCardProps {
  package_info: LexiconPackageInfo;
  importing: boolean;
  onImport: () => void;
}

export function PackageCard({ package_info, importing, onImport }: PackageCardProps) {
  const formatDate = (timestamp: number) => {
    if (!timestamp) return 'Unknown';
    return new Date(timestamp).toLocaleDateString();
  };

  const formatNumber = formatCompactNumber;

  return (
    <Card
      sx={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        bgcolor: 'rgba(17, 24, 39, 0.6)',
        border: '1px solid rgba(255, 255, 255, 0.06)',
        backdropFilter: 'blur(8px)',
        transition: 'all 0.2s',
        '&:hover': {
          transform: 'translateY(-2px)',
          borderColor: 'rgba(0, 229, 255, 0.2)',
          boxShadow: '0 4px 20px rgba(0, 0, 0, 0.3), 0 0 20px rgba(0, 229, 255, 0.05)',
        },
      }}
    >
      <CardContent sx={{ flexGrow: 1 }}>
        <Typography variant="h6" component="div" gutterBottom noWrap sx={{ color: 'text.primary' }}>
          {package_info.name}
        </Typography>

        <Typography variant="body2" sx={{ mb: 2, minHeight: 40, color: 'text.disabled' }}>
          {package_info.description?.slice(0, 100) || 'No description'}
          {package_info.description && package_info.description.length > 100 ? '...' : ''}
        </Typography>

        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mb: 2 }}>
          <Chip
            label={package_info.package_type}
            size="small"
            variant="outlined"
            sx={{
              borderColor: 'rgba(0, 229, 255, 0.25)',
              color: 'primary.main',
            }}
          />
        </Box>

        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, color: 'text.disabled' }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
            <Person fontSize="small" />
            <Typography variant="caption">{package_info.owner_username || 'Unknown'}</Typography>
          </Box>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
            <Star fontSize="small" />
            <Typography variant="caption">{formatNumber(package_info.star_count)}</Typography>
          </Box>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
            <Download fontSize="small" />
            <Typography variant="caption">{formatNumber(package_info.download_count)}</Typography>
          </Box>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
            <Update fontSize="small" />
            <Typography variant="caption">{formatDate(package_info.updated_at)}</Typography>
          </Box>
        </Box>

        <Typography variant="caption" sx={{ display: 'block', mt: 1, color: ChaosCypherNeutrals.textMuted }}>
          {package_info.version_count} version{package_info.version_count !== 1 ? 's' : ''}
        </Typography>
      </CardContent>

      <CardActions sx={{ px: 2, pb: 2 }}>
        <Button
          size="small"
          variant="outlined"
          onClick={onImport}
          disabled={importing}
          startIcon={importing ? <CircularProgress size={16} sx={{ color: 'primary.main' }} /> : <Download />}
          fullWidth
          sx={ghostButtonSx(ChaosCypherPalette.primary)}
        >
          {importing ? 'Importing...' : 'Import'}
        </Button>
      </CardActions>
    </Card>
  );
}
