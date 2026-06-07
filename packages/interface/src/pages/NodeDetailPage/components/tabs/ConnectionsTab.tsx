// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import OutgoingIcon from '@mui/icons-material/ArrowForward';
import IncomingIcon from '@mui/icons-material/ArrowBack';
import { useNavigate } from 'react-router';
import type { ConnectedNode } from '../../../../types';
import {
  ghostButtonSx,
  ghostInfoAlertSx,
  ghostInputSx,
} from '../../../../theme/ghostStyles';
import { LoadingState } from '../../../../components/LoadingState';
import { ChaosCypherPalette } from '../../../../theme/palette';

interface ConnectionsTabProps {
  connections: ConnectedNode[];
  loading: boolean;
  sortBy: string;
  hasMore: boolean;
  onSortByChange: (sortBy: string) => void;
  onLoadMore: () => void;
}

/**
 * "Connections" tab for NodeDetailPage: sortable table of connected nodes
 * with infinite "Show more" pagination.
 */
export default function ConnectionsTab({
  connections,
  loading,
  sortBy,
  hasMore,
  onSortByChange,
  onLoadMore,
}: ConnectionsTabProps) {
  const navigate = useNavigate();

  return (
    <Box>
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          mb: 2,
        }}
      >
        <Typography variant="subtitle2">Connected entities sorted by importance</Typography>
        <FormControl size="small" sx={{ minWidth: 150, ...ghostInputSx }}>
          <InputLabel>Sort by</InputLabel>
          <Select
            value={sortBy}
            label="Sort by"
            onChange={(e) => onSortByChange(e.target.value)}
          >
            <MenuItem value="edge_count">Most Connected</MenuItem>
            <MenuItem value="label">Alphabetical</MenuItem>
            <MenuItem value="relationship">By Relationship</MenuItem>
          </Select>
        </FormControl>
      </Box>

      {loading && connections.length === 0 ? (
        <LoadingState message="Loading connections..." minHeight="200px" />
      ) : connections.length === 0 ? (
        <Alert severity="info" sx={{ ...ghostInfoAlertSx }}>
          No connections found. This entity has no relationships with other entities.
        </Alert>
      ) : (
        <>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell width={40}></TableCell>
                  <TableCell>Entity</TableCell>
                  <TableCell>Relationship</TableCell>
                  <TableCell align="right">Connections</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {connections.map((conn) => (
                  <TableRow
                    key={conn.id}
                    hover
                    sx={{ cursor: 'pointer' }}
                    onClick={() => navigate(`/nodes/${conn.id}`)}
                  >
                    <TableCell>
                      {conn.direction === 'incoming' ? (
                        <IncomingIcon fontSize="small" color="primary" />
                      ) : (
                        <OutgoingIcon fontSize="small" color="secondary" />
                      )}
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2" sx={{ fontWeight: 'medium' }}>
                        {conn.label}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Chip
                        label={conn.relationship}
                        size="small"
                        color={conn.direction === 'incoming' ? 'primary' : 'secondary'}
                        variant="outlined"
                      />
                    </TableCell>
                    <TableCell align="right">
                      <Typography variant="body2" sx={{ color: 'text.secondary' }}>
                        {conn.edge_count}
                      </Typography>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>

          {hasMore && (
            <Box sx={{ display: 'flex', justifyContent: 'center', mt: 2 }}>
              <Button
                variant="outlined"
                onClick={onLoadMore}
                disabled={loading}
                sx={ghostButtonSx(ChaosCypherPalette.primary)}
              >
                {loading ? <CircularProgress size={20} /> : 'Show More'}
              </Button>
            </Box>
          )}
        </>
      )}
    </Box>
  );
}
