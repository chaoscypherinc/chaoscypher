// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from 'vitest';
import {
  SOURCE_GROUP_PREFIX,
  SOURCE_PROVENANCE_PREFIX,
  unresolvedBucketId,
  UNRESOLVED_EDGE_PREFIX,
  UNRESOLVED_ROOT_EDGE_PREFIX,
  isSyntheticEdge,
  isSyntheticNode,
  isUnresolvedBucketNode,
  bucketSourceId,
} from '../types';

/** The hub for unresolved nodes with no known source. */
const UNRESOLVED_BUCKET_ID = unresolvedBucketId(null);

describe('synthetic canvas elements', () => {
  it('treats a source root and the Unresolved hub as synthetic nodes, and nothing else', () => {
    expect(isSyntheticNode(`${SOURCE_GROUP_PREFIX}src-1`)).toBe(true);
    expect(isSyntheticNode(UNRESOLVED_BUCKET_ID)).toBe(true);
    expect(isSyntheticNode(unresolvedBucketId('src-1'))).toBe(true);
    expect(isSyntheticNode('3f2a-node')).toBe(false);
    expect(isSyntheticNode('unresolved')).toBe(false);
  });

  it('treats provenance edges and Unresolved hub edges as synthetic edges, and nothing else', () => {
    expect(isSyntheticEdge(`${SOURCE_PROVENANCE_PREFIX}src-1:n1`)).toBe(true);
    expect(isSyntheticEdge(`${UNRESOLVED_EDGE_PREFIX}n1`)).toBe(true);
    expect(isSyntheticEdge(`${UNRESOLVED_ROOT_EDGE_PREFIX}src-1`)).toBe(true);
    expect(isSyntheticEdge('edge-1')).toBe(false);
  });

  it('tells the Unresolved hub apart from a source root', () => {
    expect(isUnresolvedBucketNode(UNRESOLVED_BUCKET_ID)).toBe(true);
    expect(isUnresolvedBucketNode(unresolvedBucketId('src-1'))).toBe(true);
    expect(isUnresolvedBucketNode(`${SOURCE_GROUP_PREFIX}src-1`)).toBe(false);
    expect(isUnresolvedBucketNode('n1')).toBe(false);
  });

  it('gives each source its own hub id, and the no-source hub one of its own', () => {
    expect(unresolvedBucketId('src-1')).not.toBe(unresolvedBucketId('src-2'));
    expect(unresolvedBucketId('src-1')).not.toBe(UNRESOLVED_BUCKET_ID);
    expect(bucketSourceId(unresolvedBucketId('src-1'))).toBe('src-1');
    expect(bucketSourceId(UNRESOLVED_BUCKET_ID)).toBeNull();
  });
});
