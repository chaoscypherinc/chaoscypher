// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import {
  Alert,
  Box,
  Chip,
  Link,
  List,
  ListItem,
  Typography,
} from '@mui/material';
import SourceIcon from '@mui/icons-material/Description';
import { useNavigate } from 'react-router';
import type { Citation } from '../../../../types';
import { ghostInfoAlertSx } from '../../../../theme/ghostStyles';
import { LoadingState } from '../../../../components/LoadingState';
import { renderChunkWithHighlights } from '../../../../utils/chunkHighlight';

interface SourcesTabProps {
  citations: Citation[];
  citationsTotal: number;
  loading: boolean;
}

/**
 * "Sources" tab for NodeDetailPage: list of source citations with
 * sentence-level highlighting via `renderChunkWithHighlights`.
 */
export default function SourcesTab({ citations, citationsTotal, loading }: SourcesTabProps) {
  const navigate = useNavigate();

  if (loading) {
    return <LoadingState message="Loading sources..." minHeight="200px" />;
  }

  if (citations.length === 0) {
    return (
      <Alert severity="info" sx={{ ...ghostInfoAlertSx }}>
        No source citations found for this entity. This may mean it was created manually
        or hasn't been extracted from any documents yet.
      </Alert>
    );
  }

  return (
    <Box>
      <Typography variant="subtitle2" gutterBottom sx={{ mb: 2 }}>
        This entity was extracted from {citationsTotal} location
        {citationsTotal !== 1 ? 's' : ''} in source documents:
      </Typography>

      <List>
        {citations.map((citation) => (
          <ListItem
            key={citation.id}
            sx={{
              display: 'block',
              border: 1,
              borderColor: 'divider',
              borderRadius: 1,
              mb: 2,
              p: 2,
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
              <SourceIcon color="primary" />
              <Link
                component="button"
                variant="subtitle1"
                onClick={() => navigate(`/sources/${citation.source.id}`)}
                sx={{ fontWeight: 'bold', textAlign: 'left' }}
              >
                {citation.source.title}
              </Link>
              <Chip label={citation.source.source_type} size="small" />
            </Box>

            <Box
              sx={{
                p: 2,
                background: 'rgba(0, 0, 0, 0.4)',
                border: '1px solid rgba(255, 255, 255, 0.06)',
                borderRadius: 1.5,
                fontFamily: 'monospace',
                fontSize: '0.875rem',
                mb: 1,
                maxHeight: '200px',
                overflow: 'auto',
              }}
            >
              <Typography variant="body2" component="pre" sx={{ whiteSpace: 'pre-wrap', m: 0 }}>
                {renderChunkWithHighlights(
                  citation.chunk.content,
                  citation.citation_metadata?.sent_ref,
                  citation.chunk.chunk_metadata,
                )}
              </Typography>
            </Box>

            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, mt: 1 }}>
              {citation.chunk.page_number && (
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                  Page {citation.chunk.page_number}
                </Typography>
              )}
              {citation.chunk.section && (
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                  Section: {citation.chunk.section}
                </Typography>
              )}
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                Confidence: {(citation.confidence * 100).toFixed(0)}%
              </Typography>
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                Method: {citation.extraction_method}
              </Typography>
            </Box>
          </ListItem>
        ))}
      </List>
    </Box>
  );
}
