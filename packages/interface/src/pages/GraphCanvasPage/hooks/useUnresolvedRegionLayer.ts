// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useUnresolvedRegionLayer: draws the "Unresolved" region on two sigma
 * canvas layers.
 *
 * - Below the edges: the corral (a dashed rounded rectangle around the
 *   visible members) with the region's name.
 * - Above the nodes: a small badge on each member naming its flag(s), and,
 *   while collapsed, the canvas-only "Unresolved (N)" summary node.
 *
 * Nothing is added to the graph: the summary node is a drawing, and a click
 * on it (stage click inside its circle) expands the region.
 */

import { useEffect, useRef } from 'react';
import { useSigma } from '@react-sigma/core';
import type Graph from 'graphology';
import type { SigmaStageEventPayload } from 'sigma/types';
import type { NodeAttributes, EdgeAttributes } from '../types';
import {
  UNRESOLVED_REGION_LABEL,
  boundsOf,
  type Point,
  type UnresolvedRegion,
} from '../utils/unresolvedRegion';
import { ChaosCypherBackground, ChaosCypherNeutrals, ChaosCypherPalette } from '../../../theme/palette';
import { hexToRgba } from '../../../theme/cardStyles';

const HULL_LAYER = 'unresolvedRegionHull';
const BADGE_LAYER = 'unresolvedRegionBadges';
const REGION_COLOR = ChaosCypherPalette.warning;
const HULL_PADDING_PX = 22;
const SUMMARY_RADIUS_PX = 18;
/** Below this rendered node radius a badge is a dot, not a labelled pill. */
const BADGE_TEXT_MIN_RADIUS_PX = 5;
const FONT = 'Exo 2, sans-serif';

interface UseUnresolvedRegionLayerProps {
  graph: Graph<NodeAttributes, EdgeAttributes>;
  region: UnresolvedRegion;
  /** Expand the region (summary node clicked). */
  onExpand: () => void;
}

/** Size a layer canvas to the sigma viewport and return its 2D context. */
function prepareCanvas(
  canvas: HTMLCanvasElement,
  width: number,
  height: number,
): CanvasRenderingContext2D | null {
  const ratio = window.devicePixelRatio || 1;
  const w = Math.round(width * ratio);
  const h = Math.round(height * ratio);
  if (canvas.width !== w) canvas.width = w;
  if (canvas.height !== h) canvas.height = h;
  // CSS size in viewport px (sigma only sizes its layers on resize).
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  return ctx;
}

function drawHaloText(ctx: CanvasRenderingContext2D, text: string, x: number, y: number) {
  ctx.save();
  ctx.shadowColor = hexToRgba(ChaosCypherBackground.dark.default, 0.95);
  ctx.shadowBlur = 6;
  ctx.fillText(text, x, y);
  ctx.fillText(text, x, y);
  ctx.restore();
}

export function useUnresolvedRegionLayer({
  graph,
  region,
  onExpand,
}: UseUnresolvedRegionLayerProps): void {
  const sigma = useSigma<NodeAttributes, EdgeAttributes>();
  // Where the summary node was last drawn (viewport px), for click hit-testing.
  const summaryCenterRef = useRef<Point | null>(null);
  // The layers are created once per sigma; the draw reads the latest props.
  const latest = useRef({ graph, region, onExpand });
  useEffect(() => {
    latest.current = { graph, region, onExpand };
    sigma.scheduleRender();
  }, [sigma, graph, region, onExpand]);

  useEffect(() => {
    const hull = sigma.createCanvas(HULL_LAYER, { beforeLayer: 'edges' });
    const badges = sigma.createCanvas(BADGE_LAYER, { afterLayer: 'nodes' });
    for (const canvas of [hull, badges]) {
      canvas.style.pointerEvents = 'none';
    }

    const draw = () => {
      const { graph, region } = latest.current;
      const { width, height } = sigma.getDimensions();
      const hullCtx = prepareCanvas(hull, width, height);
      const badgeCtx = prepareCanvas(badges, width, height);
      summaryCenterRef.current = null;
      if (!hullCtx || !badgeCtx || region.count === 0) return;

      if (!region.expanded) {
        // Summary node at the members' centroid (graph coordinates).
        let sx = 0;
        let sy = 0;
        let n = 0;
        for (const id of region.memberIds) {
          if (!graph.hasNode(id)) continue;
          const attrs = graph.getNodeAttributes(id);
          sx += attrs.x;
          sy += attrs.y;
          n += 1;
        }
        if (n === 0) return;
        const center = sigma.graphToViewport({ x: sx / n, y: sy / n });
        summaryCenterRef.current = center;

        badgeCtx.beginPath();
        badgeCtx.arc(center.x, center.y, SUMMARY_RADIUS_PX * 1.6, 0, Math.PI * 2);
        badgeCtx.fillStyle = hexToRgba(REGION_COLOR, 0.12);
        badgeCtx.fill();
        badgeCtx.beginPath();
        badgeCtx.arc(center.x, center.y, SUMMARY_RADIUS_PX, 0, Math.PI * 2);
        badgeCtx.fillStyle = hexToRgba(REGION_COLOR, 0.85);
        badgeCtx.fill();
        badgeCtx.lineWidth = 2;
        badgeCtx.strokeStyle = REGION_COLOR;
        badgeCtx.stroke();

        badgeCtx.font = `bold 14px ${FONT}`;
        badgeCtx.textBaseline = 'middle';
        badgeCtx.fillStyle = ChaosCypherNeutrals.textPrimary;
        drawHaloText(badgeCtx, region.summaryLabel ?? '', center.x + SUMMARY_RADIUS_PX + 8, center.y);
        return;
      }

      // Expanded: corral around the visible members, badge on each.
      const visible: Array<Point & { r: number; id: string }> = [];
      for (const id of region.memberIds) {
        const data = sigma.getNodeDisplayData(id);
        if (!data || data.hidden) continue;
        const p = sigma.framedGraphToViewport({ x: data.x, y: data.y });
        visible.push({ id, x: p.x, y: p.y, r: sigma.scaleSize(data.size) });
      }
      const bounds = boundsOf(visible, HULL_PADDING_PX);
      if (!bounds) return;

      const w = bounds.maxX - bounds.minX;
      const h = bounds.maxY - bounds.minY;
      hullCtx.beginPath();
      hullCtx.roundRect(bounds.minX, bounds.minY, w, h, 16);
      hullCtx.fillStyle = hexToRgba(REGION_COLOR, 0.05);
      hullCtx.fill();
      hullCtx.setLineDash([6, 5]);
      hullCtx.lineWidth = 1.5;
      hullCtx.strokeStyle = hexToRgba(REGION_COLOR, 0.55);
      hullCtx.stroke();
      hullCtx.setLineDash([]);

      hullCtx.font = `bold 13px ${FONT}`;
      hullCtx.textBaseline = 'bottom';
      hullCtx.fillStyle = REGION_COLOR;
      drawHaloText(hullCtx, `${UNRESOLVED_REGION_LABEL} · ${region.count}`, bounds.minX + 4, bounds.minY - 6);

      badgeCtx.font = `600 10px ${FONT}`;
      badgeCtx.textBaseline = 'middle';
      for (const node of visible) {
        const flags = region.flagsById.get(node.id) ?? [];
        if (flags.length === 0) continue;
        const bx = node.x + node.r * 0.75;
        const by = node.y - node.r * 0.75;
        if (node.r < BADGE_TEXT_MIN_RADIUS_PX) {
          badgeCtx.beginPath();
          badgeCtx.arc(bx, by, 2.5, 0, Math.PI * 2);
          badgeCtx.fillStyle = REGION_COLOR;
          badgeCtx.fill();
          continue;
        }
        const text = flags.map((flag) => flag.label).join(' · ');
        const tw = badgeCtx.measureText(text).width;
        const ph = 14;
        badgeCtx.beginPath();
        badgeCtx.roundRect(bx, by - ph / 2, tw + 10, ph, ph / 2);
        badgeCtx.fillStyle = hexToRgba(ChaosCypherBackground.dark.default, 0.9);
        badgeCtx.fill();
        badgeCtx.lineWidth = 1;
        badgeCtx.strokeStyle = REGION_COLOR;
        badgeCtx.stroke();
        badgeCtx.fillStyle = REGION_COLOR;
        badgeCtx.fillText(text, bx + 5, by);
      }
    };

    const handleClickStage = (payload: SigmaStageEventPayload) => {
      const center = summaryCenterRef.current;
      if (!center) return;
      const dx = payload.event.x - center.x;
      const dy = payload.event.y - center.y;
      if (dx * dx + dy * dy <= SUMMARY_RADIUS_PX * SUMMARY_RADIUS_PX) latest.current.onExpand();
    };

    sigma.on('afterRender', draw);
    sigma.on('clickStage', handleClickStage);
    sigma.scheduleRender();

    return () => {
      sigma.off('afterRender', draw);
      sigma.off('clickStage', handleClickStage);
      // The container may already have killed sigma (and its layers).
      for (const layer of [HULL_LAYER, BADGE_LAYER]) {
        try {
          sigma.killLayer(layer);
        } catch {
          /* already gone */
        }
      }
    };
  }, [sigma]);
}
