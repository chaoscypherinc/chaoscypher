// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ChunkPageThumbnail: per-chunk thumbnail rail with failure placeholders.
 *
 * Branches: real image, vision-failed (yellow), render-failed (red),
 * Tier-1 generic fallback (yellow), no-image.
 *
 * Note: after migration 0034 the ``pageIsVisionFailed`` and
 * ``pageIsRenderFailed`` props are always ``false`` in production
 * (the legacy columns that drove them were dropped). The branches are
 * kept for future re-wiring against vision_page_descriptions.
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ChunkPageThumbnail } from '../ChunkPageThumbnail';

describe('<ChunkPageThumbnail />', () => {
  it('renders the real thumbnail when an image URL is available', () => {
    render(
      <ChunkPageThumbnail
        imageUrl="https://example.com/page_5.png"
        pageNumber={5}
        expectedImage={false}
        pageIsVisionFailed={false}
        pageIsRenderFailed={false}
        onExpand={() => {}}
      />,
    );

    const img = screen.getByRole('img', { name: /Page 5/i }) as HTMLImageElement;
    expect(img.src).toBe('https://example.com/page_5.png');
  });

  it("shows the yellow 'Vision failed' placeholder when pageIsVisionFailed is true", () => {
    render(
      <ChunkPageThumbnail
        imageUrl={null}
        pageNumber={5}
        expectedImage={true}
        pageIsVisionFailed={true}
        pageIsRenderFailed={false}
        onExpand={() => {}}
      />,
    );

    expect(screen.getByText('Vision failed')).toBeInTheDocument();
    expect(screen.queryByText('Render failed')).toBeNull();
  });

  it("shows the red 'Render failed' placeholder when pageIsRenderFailed is true", () => {
    render(
      <ChunkPageThumbnail
        imageUrl={null}
        pageNumber={3}
        expectedImage={true}
        pageIsVisionFailed={false}
        pageIsRenderFailed={true}
        onExpand={() => {}}
      />,
    );

    expect(screen.getByText('Render failed')).toBeInTheDocument();
    expect(screen.queryByText('Vision failed')).toBeNull();
  });

  it("falls back to the generic 'Render failed' placeholder when image is missing but page is in neither list", () => {
    // Covers future failure modes the backend hasn't classified yet.
    // Tier 1 behaviour is preserved as the fallback.
    render(
      <ChunkPageThumbnail
        imageUrl={null}
        pageNumber={9}
        expectedImage={true}
        pageIsVisionFailed={false}
        pageIsRenderFailed={false}
        onExpand={() => {}}
      />,
    );

    // The fallback re-uses the "Render failed" copy (generic placeholder).
    expect(screen.getByText('Render failed')).toBeInTheDocument();
  });

  it('renders nothing when the source did not produce per-page images', () => {
    // Text-only sources / vision-disabled sources: no thumbnail column at all.
    const { container } = render(
      <ChunkPageThumbnail
        imageUrl={null}
        pageNumber={1}
        expectedImage={false}
        pageIsVisionFailed={false}
        pageIsRenderFailed={false}
        onExpand={() => {}}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('shows the page number under the failed placeholder', () => {
    render(
      <ChunkPageThumbnail
        imageUrl={null}
        pageNumber={42}
        expectedImage={true}
        pageIsVisionFailed={true}
        pageIsRenderFailed={false}
        onExpand={() => {}}
      />,
    );
    expect(screen.getByText('p.42')).toBeInTheDocument();
  });
});
