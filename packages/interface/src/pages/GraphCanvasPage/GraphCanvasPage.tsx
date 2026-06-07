// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * GraphCanvasPage: Entry point for the knowledge graph canvas.
 *
 * Wraps GraphCanvasContent in a SigmaContainer that provides
 * the sigma/graphology context to all child hooks and components.
 */

import React, { useMemo } from 'react';
import { Box, Typography } from '@mui/material';
import { SigmaContainer } from '@react-sigma/core';
import { createNodeImageProgram } from '@sigma/node-image';
import EdgeGradientProgram from './programs/EdgeGradientProgram';
import '@react-sigma/core/lib/style.css';
import { GraphCanvasContent } from './GraphCanvasContent';
import { useGraphStore } from './hooks';
import { ChaosCypherPalette, ChaosCypherBackground, ChaosCypherNeutrals } from '../../theme/palette';
import { hexToRgba } from '../../theme/cardStyles';
import { isWebGLSupported } from '../../utils/webgl';

interface SigmaNodeRenderData {
  x: number;
  y: number;
  size?: number;
  color?: string;
  label?: string | null;
}

interface SigmaRenderSettings {
  labelSize?: number;
  labelFont?: string;
  labelWeight?: string;
  labelColor?: { color?: string; attribute?: string };
}

// Custom label renderer with dark halo for readability over edges/nodes
function drawLabel(
  context: CanvasRenderingContext2D,
  data: SigmaNodeRenderData,
  settings: SigmaRenderSettings,
) {
  const label = data.label;
  if (!label) return;

  const size = settings.labelSize || 14;
  const font = settings.labelFont || 'sans-serif';
  const weight = settings.labelWeight || 'bold';
  const textColor = settings.labelColor?.color || ChaosCypherNeutrals.textPrimary;

  context.font = `${weight} ${size}px ${font}`;
  const x = data.x + (data.size || 8) + 4;
  const y = data.y + size / 3;

  // Dark shadow halo behind text for readability
  context.save();
  context.shadowColor = hexToRgba(ChaosCypherBackground.dark.default, 0.95);
  context.shadowBlur = 6;
  context.shadowOffsetX = 0;
  context.shadowOffsetY = 0;
  context.fillStyle = textColor;
  context.fillText(label, x, y);
  // Double-pass for stronger halo effect
  context.fillText(label, x, y);
  context.restore();
}

// Custom hover renderer with glow effect and dark label background
function drawHover(
  context: CanvasRenderingContext2D,
  data: SigmaNodeRenderData,
  settings: SigmaRenderSettings,
) {
  const x = data.x;
  const y = data.y;
  const nodeSize = data.size || 8;
  const nodeColor = data.color || ChaosCypherPalette.primary;

  // Outer glow ring around hovered node
  context.save();
  context.beginPath();
  context.arc(x, y, nodeSize * 2.5, 0, Math.PI * 2);
  context.fillStyle = nodeColor;
  context.globalAlpha = 0.12;
  context.fill();
  context.restore();

  // Inner glow ring
  context.save();
  context.beginPath();
  context.arc(x, y, nodeSize * 1.6, 0, Math.PI * 2);
  context.fillStyle = nodeColor;
  context.globalAlpha = 0.2;
  context.fill();
  context.restore();

  // Label
  const label = data.label || '';
  if (!label) return;

  const size = settings.labelSize || 14;
  const font = settings.labelFont || 'sans-serif';
  const weight = settings.labelWeight || 'bold';

  context.font = `${weight} ${size}px ${font}`;
  const textWidth = context.measureText(label).width;

  const h = Math.round(size + 10);
  const labelX = x + nodeSize + 6;

  // Dark rounded rect background
  context.beginPath();
  context.roundRect(labelX - 6, y - h / 2, textWidth + 12, h, 4);
  context.fillStyle = hexToRgba(ChaosCypherBackground.dark.default, 0.92);
  context.fill();

  // Faint border matching node color
  context.strokeStyle = nodeColor;
  context.globalAlpha = 0.3;
  context.lineWidth = 1;
  context.stroke();
  context.globalAlpha = 1;

  // White text
  context.fillStyle = ChaosCypherNeutrals.textPrimary;
  context.textBaseline = 'middle';
  context.fillText(label, labelX, y);
}

const GraphCanvasPage: React.FC = () => {
  const { graph } = useGraphStore();

  // Sigma is WebGL-only. If the browser can't give us a context (hardware
  // acceleration off, GPU blocklisted, or Firefox resistFingerprinting),
  // Sigma's init dereferences a null gl and throws a cryptic getParameter
  // error that the ErrorBoundary catches as "Something went wrong". Detect
  // it up front and explain how to fix it instead. Probed once on mount.
  const webglSupported = useMemo(() => isWebGLSupported(), []);

  const sigmaSettings = useMemo(() => ({
    renderEdgeLabels: true,
    enableEdgeClickEvents: true,
    enableEdgeWheelEvents: false,
    enableEdgeHoverEvents: false,
    defaultNodeType: 'circle',
    defaultEdgeType: 'line',
    defaultEdgeSize: 1,
    nodeProgramClasses: {
      pictogram: createNodeImageProgram({
        drawingMode: 'background',
        keepWithinCircle: true,
        padding: 0.35,
        size: { mode: 'force', value: 256 },
        correctCentering: true,
      }),
    },
    edgeProgramClasses: {
      line: EdgeGradientProgram,
    },
    defaultDrawNodeLabel: drawLabel,
    defaultDrawNodeHover: drawHover,
    labelRenderedSizeThreshold: 18,
    labelDensity: 0.07,
    labelGridCellSize: 250,
    minCameraRatio: 0.05,
    maxCameraRatio: 10,
    zoomToSizeRatioFunction: (ratio: number) => ratio,
    itemSizesReference: 'positions' as const,
    zoomDuration: 200,
    inertiaDuration: 0,
    inertiaRatio: 1,
    enableCameraRotation: false,
    stagePadding: 40,
  }), []);

  return (
    <>
      {/* Desktop-only message for xs/sm viewports.
          The Sigma canvas assumes ≥1024px for usable interaction
          (pan, zoom, edge selection, properties panel). Rather than
          render an unusable canvas on phones, surface a brief notice. */}
      <Box
        sx={{
          display: { xs: 'flex', md: 'none' },
          alignItems: 'center',
          justifyContent: 'center',
          height: 'calc(100vh - 64px)',
          px: 3,
          textAlign: 'center',
        }}
      >
        <Box sx={{ maxWidth: 360 }}>
          <Typography variant="h6" sx={{ mb: 1, color: 'text.primary' }}>
            Desktop only
          </Typography>
          <Typography variant="body2" sx={{ color: 'text.secondary' }}>
            The graph canvas requires a desktop screen (≥1024px) for usable
            pan, zoom, and selection. Other pages remain readable on this
            device.
          </Typography>
        </Box>
      </Box>
      <Box sx={{ display: { xs: 'none', md: 'block' }, height: 'calc(100vh - 64px)' }}>
        {webglSupported ? (
          <SigmaContainer
            graph={graph}
            settings={sigmaSettings}
            style={{ width: '100%', height: 'calc(100vh - 64px)' }}
          >
            <GraphCanvasContent graph={graph} />
          </SigmaContainer>
        ) : (
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              height: '100%',
              px: 3,
              textAlign: 'center',
            }}
          >
            <Box sx={{ maxWidth: 480 }}>
              <Typography variant="h6" sx={{ mb: 1, color: 'text.primary' }}>
                WebGL unavailable
              </Typography>
              <Typography variant="body2" sx={{ color: 'text.secondary', mb: 1 }}>
                The graph canvas renders with WebGL, which your browser isn't
                providing right now. The rest of the app works without it.
              </Typography>
              <Typography variant="body2" sx={{ color: 'text.secondary' }}>
                To fix it: enable hardware acceleration in your browser
                settings, and in Firefox check that{' '}
                <Box component="code" sx={{ fontFamily: 'monospace' }}>
                  privacy.resistFingerprinting
                </Box>{' '}
                is off (it blocks WebGL). Then reload this page.
              </Typography>
            </Box>
          </Box>
        )}
      </Box>
    </>
  );
};

export default GraphCanvasPage;
