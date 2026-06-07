// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * PackageTable — Ghost-styled table for displaying Lexicon packages.
 */
import {
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Button,
  Chip,
  CircularProgress,
  alpha,
} from '@mui/material';
import Download from '@mui/icons-material/Download';
import type { LexiconPackageInfo } from '../../types/lexicon';
import { formatCompactNumber } from '../../utils/formatters';
import { ghostButtonSx, ghostTableHeadCellSx } from '../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../theme/palette';

interface PackageTableProps {
  packages: LexiconPackageInfo[];
  importingId: string | null;
  onImport: (ownerUsername: string, repoName: string) => void;
}

export function PackageTable({ packages, importingId, onImport }: PackageTableProps) {
  const formatDate = (timestamp: number) => {
    if (!timestamp) return 'Unknown';
    return new Date(timestamp).toLocaleDateString();
  };

  const formatNumber = formatCompactNumber;

  const getPackageKey = (pkg: LexiconPackageInfo) => `${pkg.owner_username}/${pkg.name}`;

  return (
    <TableContainer
      sx={{
        bgcolor: 'rgba(17, 24, 39, 0.4)',
        border: '1px solid rgba(255, 255, 255, 0.06)',
        borderRadius: '8px',
        backdropFilter: 'blur(8px)',
      }}
    >
      <Table>
        <TableHead>
          <TableRow>
            <TableCell sx={ghostTableHeadCellSx}>Name</TableCell>
            <TableCell sx={ghostTableHeadCellSx}>Description</TableCell>
            <TableCell sx={ghostTableHeadCellSx}>Owner</TableCell>
            <TableCell sx={ghostTableHeadCellSx}>Type</TableCell>
            <TableCell align="right" sx={ghostTableHeadCellSx}>Stars</TableCell>
            <TableCell align="right" sx={ghostTableHeadCellSx}>Downloads</TableCell>
            <TableCell sx={ghostTableHeadCellSx}>Updated</TableCell>
            <TableCell align="center" sx={ghostTableHeadCellSx}>Actions</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {packages.map((pkg) => {
            const pkgKey = getPackageKey(pkg);
            return (
              <TableRow
                key={pkg.id}
                sx={{
                  '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.03)' },
                  '& td': { borderColor: 'rgba(255, 255, 255, 0.04)' },
                }}
              >
                <TableCell component="th" scope="row" sx={{ color: 'text.primary' }}>
                  {pkg.name}
                </TableCell>
                <TableCell sx={{ maxWidth: 300, color: 'text.secondary' }}>
                  {pkg.description?.slice(0, 80) || 'No description'}
                  {pkg.description && pkg.description.length > 80 ? '...' : ''}
                </TableCell>
                <TableCell sx={{ color: 'text.secondary' }}>{pkg.owner_username || 'Unknown'}</TableCell>
                <TableCell>
                  <Chip
                    label={pkg.package_type}
                    size="small"
                    variant="outlined"
                    sx={{ borderColor: alpha(ChaosCypherPalette.primary, 0.25), color: 'primary.main' }}
                  />
                </TableCell>
                <TableCell align="right" sx={{ color: 'text.secondary' }}>{formatNumber(pkg.star_count)}</TableCell>
                <TableCell align="right" sx={{ color: 'text.secondary' }}>{formatNumber(pkg.download_count)}</TableCell>
                <TableCell sx={{ color: 'text.secondary' }}>{formatDate(pkg.updated_at)}</TableCell>
                <TableCell align="center">
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() => onImport(pkg.owner_username, pkg.name)}
                    disabled={importingId === pkgKey}
                    startIcon={
                      importingId === pkgKey ? (
                        <CircularProgress size={16} sx={{ color: 'primary.main' }} />
                      ) : (
                        <Download />
                      )
                    }
                    sx={ghostButtonSx(ChaosCypherPalette.primary)}
                  >
                    Import
                  </Button>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
