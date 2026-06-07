// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * isWebGLSupported — does this browser hand us a usable WebGL context?
 *
 * Sigma (the graph renderer) is WebGL-only; with no context its init
 * dereferences a null gl and throws a cryptic `getParameter` error that
 * crashes the page. Callers use this to render a helpful notice instead.
 *
 * We probe both webgl2 and webgl1 on a throwaway canvas. getContext can
 * itself throw — Firefox's privacy.resistFingerprinting blocks WebGL that
 * way — so the probe is wrapped in try/catch.
 */
export function isWebGLSupported(): boolean {
  try {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl2') ?? canvas.getContext('webgl');
    return gl != null;
  } catch {
    return false;
  }
}
