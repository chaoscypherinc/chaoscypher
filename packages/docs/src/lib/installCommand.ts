// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * The one `docker run` the homepage quotes, in its two forms.
 *
 * The hero's one-liner and the Get Started card's complete command used to be
 * typed apart, and the one-liner lost `--add-host` (the 2026-10-29 docs audit):
 * a Linux Docker Engine user who copied it could not reach Ollama on the host,
 * because the image points at `host.docker.internal`. Both forms are built from
 * the same pieces here, so a flag added to one reaches the other.
 * `test_public_docs_guards.py` checks that every public `docker run` of the
 * image, this file included, carries the host-gateway flag.
 */
export const IMAGE = "ghcr.io/chaoscypherinc/chaoscypher";
export const DATA_VOLUME = "-v chaoscypher-data:/data";
export const HOST_GATEWAY = "--add-host=host.docker.internal:host-gateway";

/** HTTP only, unnamed container: the quickest try. */
export const QUICK_RUN_CMD = `docker run -d -p 80:80 ${DATA_VOLUME} ${HOST_GATEWAY} \\\n${IMAGE}`;

/** Named container, HTTPS-ready: the command the install docs give. */
export const FULL_RUN_CMD = [
  "docker run -d --name chaoscypher \\",
  "  -p 80:80 \\",
  "  -p 443:443 \\",
  `  ${DATA_VOLUME} \\`,
  `  ${HOST_GATEWAY} \\`,
  `  ${IMAGE}:latest`,
].join("\n");
