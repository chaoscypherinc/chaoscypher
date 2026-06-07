// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useZoomIcons: Zoom-adaptive icon visibility with hysteresis.
 *
 * Hides template icon glyphs when nodes render too small for the
 * glyph to look good.  A hysteresis deadband (two thresholds) absorbs
 * floating-point drift near the boundary so a slow zoom across the
 * threshold doesn't flap.
 *
 * Important: this graph configures `itemSizesReference: 'positions'`
 * (GraphCanvasPage.tsx), which makes Sigma's `scaleSize` depend on the
 * cached `graphToViewportRatio` member that is only refreshed during
 * `render()`.  Reading `scaleSize` between renders or in response to a
 * pure pan event can return values that are slightly inconsistent with
 * what's actually on screen.  To stay correct, the camera-update
 * handler below recomputes only when the camera **ratio** changes
 * (panning never triggers work) and reads ratio directly from camera
 * state -- which is synchronous, not deferred to the next render.
 */

import { useEffect, useRef } from 'react';
import { useSigma } from '@react-sigma/core';
import type { NodeAttributes, EdgeAttributes } from '../types';

/** Hide icons when rendered size drops below this (px). */
const ICON_VISIBILITY_HIDE_BELOW_PX = 9;
/** Show icons when rendered size climbs above this (px). */
const ICON_VISIBILITY_SHOW_ABOVE_PX = 10;

/**
 * Node sizes currently emitted by transformers.ts.  The zoom-adaptive
 * icon visibility effect tracks each size independently, so icons on
 * big and small nodes toggle at their natural zoom levels.  Keep in
 * sync with applyDegreeSizing (MIN_SIZE=3, MAX_SIZE=8) and
 * SOURCE_GROUP_SIZE (12) in transformers.ts.
 */
const ICON_VISIBILITY_TRACKED_SIZES = [3, 4, 5, 6, 7, 8, 12];

/**
 * Manage zoom-adaptive icon visibility for sigma pictogram nodes.
 *
 * Returns a mutable ref whose Map values indicate whether icons at
 * each tracked size should be visible.  The node reducer reads this
 * ref to decide whether to swap `pictogram` nodes to plain `circle`.
 */
export function useZoomIcons(): React.RefObject<Map<number, boolean>> {
  const sigma = useSigma<NodeAttributes, EdgeAttributes>();
  const iconVisibleBySizeRef = useRef<Map<number, boolean>>(new Map());

  useEffect(() => {
    const camera = sigma.getCamera();

    // Treat ratios that round to the same value at 1e-9 precision as
    // identical, so genuinely-equal floats don't get split by tiny noise.
    const normalizeRatio = (r: number) => Math.round(r * 1e9) / 1e9;

    const recomputeVisibilityForCurrentZoom = () => {
      let changed = false;
      for (const size of ICON_VISIBILITY_TRACKED_SIZES) {
        const renderedPx = sigma.scaleSize(size);
        const wasVisible = iconVisibleBySizeRef.current.get(size) === true;
        const nowVisible = wasVisible
          ? renderedPx >= ICON_VISIBILITY_HIDE_BELOW_PX // stay visible until clearly below
          : renderedPx >= ICON_VISIBILITY_SHOW_ABOVE_PX; // reappear only when clearly above
        if (nowVisible !== wasVisible) {
          iconVisibleBySizeRef.current.set(size, nowVisible);
          changed = true;
        }
      }
      return changed;
    };

    // Seed the ref + initial last-ratio from the current camera state, so
    // the first reducer run reads correct values. We deliberately use the
    // SHOW threshold for the seed so initial state is conservative.
    let lastRatio = normalizeRatio(camera.getState().ratio);
    for (const size of ICON_VISIBILITY_TRACKED_SIZES) {
      const renderedPx = sigma.scaleSize(size);
      iconVisibleBySizeRef.current.set(size, renderedPx >= ICON_VISIBILITY_SHOW_ABOVE_PX);
    }

    const handleCameraUpdate = () => {
      const currentRatio = normalizeRatio(camera.getState().ratio);
      // Pure pan: ratio unchanged. Skip everything — no recompute, no
      // refresh, no scaleSize call. This is the key fix for the flicker.
      if (currentRatio === lastRatio) return;
      lastRatio = currentRatio;

      if (recomputeVisibilityForCurrentZoom()) {
        sigma.refresh();
      }
    };

    camera.on('updated', handleCameraUpdate);
    return () => {
      camera.off('updated', handleCameraUpdate);
    };
  }, [sigma]);

  return iconVisibleBySizeRef;
}
