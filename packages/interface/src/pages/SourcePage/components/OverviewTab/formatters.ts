// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * OverviewTab-specific formatter re-exports.
 *
 * Currently a single re-export of ``cleanTypeName`` from the centralized
 * formatters module. The dedicated location exists so OverviewTab-only
 * helpers can be added here without having to touch the global formatters
 * module.
 */

export { cleanTypeName } from '../../../../utils/formatters';
