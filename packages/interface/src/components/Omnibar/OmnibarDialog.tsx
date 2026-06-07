// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * OmnibarDialog
 *
 * Renders the Omnibar dropdown by reading open/close state from context.
 * Designed to be placed once inside the Layout tree.
 */

import { useOmnibar } from './useOmnibar';
import { Omnibar } from './Omnibar';

/** Renders the Omnibar dropdown using context state. */
export function OmnibarDialog() {
  const { isOpen, close, openKey, openOptions, anchorEl } = useOmnibar();
  return (
    <Omnibar
      isOpen={isOpen}
      onClose={close}
      openKey={openKey}
      anchorEl={anchorEl}
      initialQuery={openOptions.initialQuery}
      initialMode={openOptions.initialMode}
    />
  );
}
