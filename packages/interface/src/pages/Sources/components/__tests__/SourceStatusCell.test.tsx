// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for the merged status chip in the sources list row.
 *
 * Until 2026-05-11 the active-vs-disabled chip and the vector-search
 * "Search ready" badge were separate cells. They were merged so the
 * single chip carries both signals: green "Active" when everything is
 * fine, warning "Search retrying" while the orphan-sweep worker is
 * retrying indexing, and error "Search failed" when retries are
 * exhausted. The tooltip explains both the visibility and the
 * indexing state.
 *
 * These tests pin the new label/colour/tooltip matrix; together with
 * the existing SearchStatusBadge tests (still used on the source-detail
 * header) they cover the full set of vector-status presentations.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { SourceStatusCell } from '../SourceStatusCell';
import type { UnifiedSource } from '../../../../types';

/**
 * Build a minimally-populated active source. Tests override the bits
 * they care about (vector status, enabled flag). recovery_attempts stays
 * at 0 so we don't render the recovery sub-badge (it has its own context
 * dependencies we don't want to thread through every test).
 */
function makeActiveSource(overrides: Partial<UnifiedSource> = {}): UnifiedSource {
  return {
    id: 'src-1',
    stage: 'active',
    title: 'doc.pdf',
    source_type: 'pdf',
    size: 1024,
    status: 'completed',
    created_at: '2026-05-11T00:00:00Z',
    active: {
      chunk_count: 12,
      enabled: true,
    },
    ...overrides,
  };
}

describe('<SourceStatusCell /> — merged active + vector-status chip', () => {
  it("renders green 'Active' when vector status is 'indexed'", () => {
    render(
      <SourceStatusCell
        source={makeActiveSource({
          vector_indexing_status: 'indexed',
          vector_indexed_at: '2026-05-11T12:00:00Z',
        })}
      />,
    );
    expect(screen.getByText('Active')).toBeInTheDocument();
  });

  it("renders green 'Active' when vector status is 'pending' (don't alarm on transient state)", () => {
    render(
      <SourceStatusCell
        source={makeActiveSource({ vector_indexing_status: 'pending' })}
      />,
    );
    expect(screen.getByText('Active')).toBeInTheDocument();
  });

  it("renders green 'Active' when vector status is missing (legacy / not yet plumbed)", () => {
    render(<SourceStatusCell source={makeActiveSource()} />);
    expect(screen.getByText('Active')).toBeInTheDocument();
  });

  it("renders yellow 'Search retrying' when vector status is 'degraded'", () => {
    render(
      <SourceStatusCell
        source={makeActiveSource({ vector_indexing_status: 'degraded' })}
      />,
    );
    expect(screen.getByText('Search retrying')).toBeInTheDocument();
    expect(screen.queryByText('Active')).not.toBeInTheDocument();
  });

  it("renders red 'Search failed' when vector status is 'failed'", () => {
    render(
      <SourceStatusCell
        source={makeActiveSource({ vector_indexing_status: 'failed' })}
      />,
    );
    expect(screen.getByText('Search failed')).toBeInTheDocument();
    expect(screen.queryByText('Active')).not.toBeInTheDocument();
  });

  it("renders 'Disabled' regardless of vector status (disabled wins)", () => {
    render(
      <SourceStatusCell
        source={makeActiveSource({
          active: { chunk_count: 12, enabled: false },
          vector_indexing_status: 'failed',
        })}
      />,
    );
    expect(screen.getByText('Disabled')).toBeInTheDocument();
    // The vector problem is hidden — the source isn't searchable anyway.
    expect(screen.queryByText('Search failed')).not.toBeInTheDocument();
  });

  it("falls back to 'Active' for an unrecognised future vector status", () => {
    // Defensive: a newer backend may add states we don't know about. We
    // don't want to crash or invent alarming red chips; treat unknown as
    // "no known problem".
    render(
      <SourceStatusCell
        source={makeActiveSource({ vector_indexing_status: 'quantum-flux' })}
      />,
    );
    expect(screen.getByText('Active')).toBeInTheDocument();
  });
});

describe('SourceStatusCell — extraction-failed surfacing', () => {
  it('renders Error chip and message when status=error from chunk failure', () => {
    const source = {
      id: 'src_test',
      status: 'error',
      stage: 'error',
      is_paused: false,
      ingestion: {
        error_stage: 'extraction',
        error_message:
          'Extraction failed: 16 of 16 chunks failed. Top errors: ' +
          "model 'qwen3:30b-instruct' not found (x16)",
      },
      recovery_attempts: 0,
    } as unknown as UnifiedSource;

    render(<SourceStatusCell source={source} />);

    expect(screen.getByText('Error')).toBeInTheDocument();
    expect(
      screen.getByText(/qwen3:30b-instruct.*not found/i),
    ).toBeInTheDocument();
  });
});

describe('SourceStatusCell — awaiting_confirmation', () => {
  function makeAwaiting(overrides: Partial<UnifiedSource> = {}): UnifiedSource {
    return {
      id: 'src-await',
      stage: 'queued',
      title: 'paper.pdf',
      source_type: 'pdf',
      size: 2048,
      status: 'awaiting_confirmation',
      created_at: '2026-05-28T00:00:00Z',
      is_paused: false,
      confirmation_required: true,
      detection_confidence: 0.82,
      detection_ranking: [{ domain: 'science', score: 4.2 }],
      ...overrides,
    };
  }

  it("renders an actionable 'Confirm domain' chip (NOT the processing spinner)", () => {
    render(<SourceStatusCell source={makeAwaiting()} />);
    expect(screen.getByRole('button', { name: /confirm domain/i })).toBeInTheDocument();
    // The default processing fallback must not render for this status.
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  });

  it('invokes onConfirmExtraction with the source when the chip is clicked', () => {
    const onConfirm = vi.fn();
    const source = makeAwaiting();
    render(<SourceStatusCell source={source} onConfirmExtraction={onConfirm} />);
    fireEvent.click(screen.getByRole('button', { name: /confirm domain/i }));
    expect(onConfirm).toHaveBeenCalledWith(source);
  });

  it('does not propagate the chip click to the row', () => {
    const onRowClick = vi.fn();
    render(
      <div onClick={onRowClick}>
        <SourceStatusCell source={makeAwaiting()} onConfirmExtraction={vi.fn()} />
      </div>,
    );
    fireEvent.click(screen.getByRole('button', { name: /confirm domain/i }));
    expect(onRowClick).not.toHaveBeenCalled();
  });
});
