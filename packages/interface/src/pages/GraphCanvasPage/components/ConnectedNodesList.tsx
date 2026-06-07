// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ConnectedNodesList: Shows edges and connected nodes for a selected node.
 *
 * Reads connections directly from the graphology graph instance.
 * Grouped by direction (outgoing / incoming) with relationship labels
 * and "Go" buttons to navigate to connected nodes.
 */

import React, { useMemo, useState } from 'react';
import {
  Box,
  Typography,
  Button,
  Divider,
} from '@mui/material';
import ArrowForwardIcon from '@mui/icons-material/ArrowForwardOutlined';
import ArrowBackIcon from '@mui/icons-material/ArrowBackOutlined';
import ExpandMoreIcon from '@mui/icons-material/ExpandMoreOutlined';
import type Graph from 'graphology';
import type { NodeAttributes, EdgeAttributes } from '../types';
import { isProvenanceEdge } from '../types';
import { ChaosCypherPalette } from '../../../theme/palette';
import { hexToRgba } from '../../../theme/cardStyles';
import { Overlays } from '../../../theme/overlays';

interface ConnectedNode {
  nodeId: string;
  nodeLabel: string;
  edgeLabel: string;
  direction: 'outgoing' | 'incoming';
}

interface ConnectedNodesListProps {
  /** The graphology graph instance. */
  graph: Graph<NodeAttributes, EdgeAttributes>;
  /** The currently selected node ID. */
  nodeId: string;
  /** Callback to navigate to a connected node. */
  onSelectNode: (nodeId: string) => void;
}

const INITIAL_SHOW_COUNT = 8;

/**
 * Renders a list of nodes connected to the selected node,
 * grouped by direction with relationship labels.
 */
const ConnectedNodesList: React.FC<ConnectedNodesListProps> = ({
  graph,
  nodeId,
  onSelectNode,
}) => {
  const [expanded, setExpanded] = useState(false);

  const connections = useMemo(() => {
    if (!graph.hasNode(nodeId)) return [];

    const result: ConnectedNode[] = [];

    graph.forEachEdge(nodeId, (edge, attrs, source, target) => {
      if (isProvenanceEdge(edge)) return;

      const isOutgoing = source === nodeId;
      const connectedId = isOutgoing ? target : source;

      if (!graph.hasNode(connectedId)) return;

      const connectedAttrs = graph.getNodeAttributes(connectedId);

      result.push({
        nodeId: connectedId,
        nodeLabel: connectedAttrs.title || connectedAttrs.label || 'Untitled',
        edgeLabel: attrs.label || '',
        direction: isOutgoing ? 'outgoing' : 'incoming',
      });
    });

    // Sort: outgoing first, then alphabetical by label
    result.sort((a, b) => {
      if (a.direction !== b.direction) return a.direction === 'outgoing' ? -1 : 1;
      return a.nodeLabel.localeCompare(b.nodeLabel);
    });

    return result;
  }, [graph, nodeId]);

  if (connections.length === 0) {
    return (
      <Box sx={{ mt: 1 }}>
        <Typography variant="body2" sx={{ color: 'text.secondary', fontStyle: 'italic' }}>
          No connections
        </Typography>
      </Box>
    );
  }

  const visibleConnections = expanded
    ? connections
    : connections.slice(0, INITIAL_SHOW_COUNT);
  const hasMore = connections.length > INITIAL_SHOW_COUNT;

  return (
    <Box sx={{ mt: 1 }}>
      {visibleConnections.map((conn, idx) => (
        <Box
          key={`${conn.nodeId}-${idx}`}
          sx={{
            display: 'flex',
            alignItems: 'center',
            gap: 1,
            py: 0.75,
            px: 1,
            borderRadius: 1,
            '&:hover': { bgcolor: Overlays.subtle.dark },
          }}
        >
          {/* Direction icon */}
          {conn.direction === 'outgoing' ? (
            <ArrowForwardIcon sx={{ fontSize: 14, color: 'primary.main', flexShrink: 0 }} />
          ) : (
            <ArrowBackIcon sx={{ fontSize: 14, color: 'info.main', flexShrink: 0 }} />
          )}

          {/* Relationship + node name */}
          <Box sx={{ flex: 1, minWidth: 0 }}>
            {conn.edgeLabel && (
              <Typography
                variant="caption"
                sx={{
                  color: 'text.secondary',
                  fontStyle: 'italic',
                  display: 'block',
                  lineHeight: 1.2,
                }}
              >
                {conn.edgeLabel}
              </Typography>
            )}
            <Typography
              variant="body2"
              noWrap
              sx={{ lineHeight: 1.3 }}
            >
              {conn.nodeLabel}
            </Typography>
          </Box>

          {/* Go button */}
          <Button
            size="small"
            onClick={() => onSelectNode(conn.nodeId)}
            sx={{
              minWidth: 0,
              px: 1,
              py: 0.25,
              fontSize: '0.7rem',
              color: 'primary.main',
              borderColor: hexToRgba(ChaosCypherPalette.primary, 0.3),
              '&:hover': {
                bgcolor: hexToRgba(ChaosCypherPalette.primary, 0.08),
                borderColor: 'primary.main',
              },
            }}
            variant="outlined"
          >
            Go &rarr;
          </Button>
        </Box>
      ))}

      {hasMore && (
        <>
          <Divider sx={{ my: 0.5 }} />
          <Button
            size="small"
            onClick={() => setExpanded(!expanded)}
            endIcon={<ExpandMoreIcon sx={{
              transform: expanded ? 'rotate(180deg)' : 'none',
              transition: 'transform 0.2s',
            }} />}
            sx={{
              width: '100%',
              color: 'text.secondary',
              fontSize: '0.75rem',
              '&:hover': { bgcolor: Overlays.subtle.dark },
            }}
          >
            {expanded ? 'Show less' : `Show all ${connections.length} connections`}
          </Button>
        </>
      )}
    </Box>
  );
};

export default ConnectedNodesList;
