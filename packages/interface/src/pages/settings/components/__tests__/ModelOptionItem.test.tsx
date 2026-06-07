// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for ModelOptionItem.tsx
 *
 * Strategy: render the real component directly (no mocks needed — pure
 * presentational). Each test targets a distinct branch: installed vs not,
 * provider 'ollama' vs 'local', pullProgress variants, active model chip,
 * and description formatting. Callbacks are vi.fn() stubs asserted on args.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ModelOptionItem } from '../ModelOptionItem';
import type { EmbeddingOption } from '../../hooks/useEmbeddingModels';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeOption(overrides: Partial<EmbeddingOption> = {}): EmbeddingOption {
  return {
    id: 'nomic-embed-text',
    name: 'Nomic Embed Text',
    description: 'A compact embedding model',
    group: 'curated',
    installed: false,
    ...overrides,
  };
}

function makeCallbacks() {
  return {
    onMenuOpen: vi.fn(),
    onPullModel: vi.fn(),
    onLocalDownload: vi.fn(),
    onLocalDelete: vi.fn(),
  };
}

// ---------------------------------------------------------------------------
// Installed status icon
// ---------------------------------------------------------------------------

describe('installed status icon', () => {
  it('shows CheckCircle (success) icon when installed=true', () => {
    const cbs = makeCallbacks();
    const { container } = render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ installed: true })}
        activeModelId="other-model"
        provider="ollama"
        isDownloading={false}
        {...cbs}
      />,
    );
    // CheckCircleIcon renders an svg with data-testid or class; assert by absence of Download on left
    // The easiest reliable check: the More actions button is present (installed ollama branch)
    // and no download button with that model name
    expect(screen.getByRole('button', { name: 'More actions' })).toBeInTheDocument();
    // Container should not have the download aria-label for this model
    expect(container.querySelector('[aria-label="Download Nomic Embed Text"]')).toBeNull();
  });

  it('shows Download (disabled-color) icon indicator when installed=false', () => {
    const cbs = makeCallbacks();
    render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ installed: false })}
        activeModelId="other-model"
        provider="ollama"
        isDownloading={false}
        {...cbs}
      />,
    );
    // The action download button is present for the not-installed ollama case
    expect(screen.getByRole('button', { name: 'Download Nomic Embed Text' })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Ollama + installed: "More actions" menu button
// ---------------------------------------------------------------------------

describe('ollama + installed: More actions button', () => {
  it('renders "More actions" IconButton', () => {
    const cbs = makeCallbacks();
    render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ installed: true })}
        activeModelId="other-model"
        provider="ollama"
        isDownloading={false}
        {...cbs}
      />,
    );
    expect(screen.getByRole('button', { name: 'More actions' })).toBeInTheDocument();
  });

  it('clicking "More actions" calls onMenuOpen(event, option.id)', () => {
    const cbs = makeCallbacks();
    render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ installed: true })}
        activeModelId="other-model"
        provider="ollama"
        isDownloading={false}
        {...cbs}
      />,
    );
    const btn = screen.getByRole('button', { name: 'More actions' });
    fireEvent.click(btn);
    expect(cbs.onMenuOpen).toHaveBeenCalledTimes(1);
    const [, modelId] = cbs.onMenuOpen.mock.calls[0] as [unknown, string];
    expect(modelId).toBe('nomic-embed-text');
  });

  it('does NOT call onPullModel or onLocalDownload when clicking More actions', () => {
    const cbs = makeCallbacks();
    render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ installed: true })}
        activeModelId="other-model"
        provider="ollama"
        isDownloading={false}
        {...cbs}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    expect(cbs.onPullModel).not.toHaveBeenCalled();
    expect(cbs.onLocalDownload).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Ollama + NOT installed + no pullProgress: download button
// ---------------------------------------------------------------------------

describe('ollama + not installed + no pullProgress: download button', () => {
  it('renders download button with aria-label "Download ${option.name}"', () => {
    const cbs = makeCallbacks();
    render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ installed: false })}
        activeModelId="other-model"
        provider="ollama"
        isDownloading={false}
        {...cbs}
      />,
    );
    expect(screen.getByRole('button', { name: 'Download Nomic Embed Text' })).toBeInTheDocument();
  });

  it('clicking download button calls onPullModel(option.id)', () => {
    const cbs = makeCallbacks();
    render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ installed: false })}
        activeModelId="other-model"
        provider="ollama"
        isDownloading={false}
        {...cbs}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Download Nomic Embed Text' }));
    expect(cbs.onPullModel).toHaveBeenCalledTimes(1);
    expect(cbs.onPullModel).toHaveBeenCalledWith('nomic-embed-text');
  });

  it('does not render More actions or LinearProgress', () => {
    const cbs = makeCallbacks();
    render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ installed: false })}
        activeModelId="other-model"
        provider="ollama"
        isDownloading={false}
        {...cbs}
      />,
    );
    expect(screen.queryByRole('button', { name: 'More actions' })).not.toBeInTheDocument();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Ollama + pullProgress: LinearProgress and status text
// ---------------------------------------------------------------------------

describe('ollama + pullProgress: progress bar', () => {
  it('shows determinate LinearProgress and percentage when total > 0', () => {
    const cbs = makeCallbacks();
    render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ installed: false })}
        activeModelId="other-model"
        provider="ollama"
        isDownloading={false}
        pullProgress={{ status: 'pulling manifest', completed: 50, total: 100 }}
        {...cbs}
      />,
    );
    const progressbar = screen.getByRole('progressbar');
    expect(progressbar).toBeInTheDocument();
    // MUI LinearProgress with variant="determinate" sets aria-valuenow
    expect(progressbar).toHaveAttribute('aria-valuenow', '50');
    // Percentage text suffix
    expect(screen.getByText(/\(50%\)/)).toBeInTheDocument();
    // Status text
    expect(screen.getByText(/pulling manifest/)).toBeInTheDocument();
  });

  it('shows indeterminate LinearProgress (no aria-valuenow) when total === 0', () => {
    const cbs = makeCallbacks();
    render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ installed: false })}
        activeModelId="other-model"
        provider="ollama"
        isDownloading={false}
        pullProgress={{ status: 'connecting', completed: 0, total: 0 }}
        {...cbs}
      />,
    );
    const progressbar = screen.getByRole('progressbar');
    expect(progressbar).toBeInTheDocument();
    // Indeterminate has no aria-valuenow
    expect(progressbar).not.toHaveAttribute('aria-valuenow');
    // No percentage suffix
    expect(screen.queryByText(/\(%\)/)).not.toBeInTheDocument();
    expect(screen.getByText(/connecting/)).toBeInTheDocument();
  });

  it('hides download button when pullProgress is active', () => {
    const cbs = makeCallbacks();
    render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ installed: false })}
        activeModelId="other-model"
        provider="ollama"
        isDownloading={false}
        pullProgress={{ status: 'downloading', completed: 10, total: 200 }}
        {...cbs}
      />,
    );
    expect(screen.queryByRole('button', { name: 'Download Nomic Embed Text' })).not.toBeInTheDocument();
  });

  it('computes percentage correctly for non-round values: completed=1, total=3 → (33%)', () => {
    const cbs = makeCallbacks();
    render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ installed: false })}
        activeModelId="other-model"
        provider="ollama"
        isDownloading={false}
        pullProgress={{ status: 'pulling', completed: 1, total: 3 }}
        {...cbs}
      />,
    );
    // Math.round(1/3 * 100) = 33
    expect(screen.getByText(/\(33%\)/)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Local + NOT installed + isDownloading=true → CircularProgress
// ---------------------------------------------------------------------------

describe('local + not installed + isDownloading=true', () => {
  it('shows CircularProgress spinner, not a download button', () => {
    const cbs = makeCallbacks();
    render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ installed: false })}
        activeModelId="other-model"
        provider="local"
        isDownloading={true}
        {...cbs}
      />,
    );
    // CircularProgress renders role="progressbar"
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Download model' })).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Local + NOT installed + isDownloading=false → "Download model" button
// ---------------------------------------------------------------------------

describe('local + not installed + isDownloading=false', () => {
  it('renders "Download model" button', () => {
    const cbs = makeCallbacks();
    render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ installed: false })}
        activeModelId="other-model"
        provider="local"
        isDownloading={false}
        {...cbs}
      />,
    );
    expect(screen.getByRole('button', { name: 'Download model' })).toBeInTheDocument();
  });

  it('clicking "Download model" calls onLocalDownload(option.id)', () => {
    const cbs = makeCallbacks();
    render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ installed: false })}
        activeModelId="other-model"
        provider="local"
        isDownloading={false}
        {...cbs}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Download model' }));
    expect(cbs.onLocalDownload).toHaveBeenCalledTimes(1);
    expect(cbs.onLocalDownload).toHaveBeenCalledWith('nomic-embed-text');
  });

  it('does not call onLocalDelete or onMenuOpen', () => {
    const cbs = makeCallbacks();
    render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ installed: false })}
        activeModelId="other-model"
        provider="local"
        isDownloading={false}
        {...cbs}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Download model' }));
    expect(cbs.onLocalDelete).not.toHaveBeenCalled();
    expect(cbs.onMenuOpen).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Local + installed + id !== activeModelId → "Delete model" button
// ---------------------------------------------------------------------------

describe('local + installed + id !== activeModelId', () => {
  it('renders "Delete model" button', () => {
    const cbs = makeCallbacks();
    render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ installed: true })}
        activeModelId="some-other-model"
        provider="local"
        isDownloading={false}
        {...cbs}
      />,
    );
    expect(screen.getByRole('button', { name: 'Delete model' })).toBeInTheDocument();
  });

  it('clicking "Delete model" calls onLocalDelete(option.id, event)', () => {
    const cbs = makeCallbacks();
    render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ installed: true })}
        activeModelId="some-other-model"
        provider="local"
        isDownloading={false}
        {...cbs}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Delete model' }));
    expect(cbs.onLocalDelete).toHaveBeenCalledTimes(1);
    const [modelId] = cbs.onLocalDelete.mock.calls[0] as [string, unknown];
    expect(modelId).toBe('nomic-embed-text');
    // Second arg is the event
    expect(cbs.onLocalDelete.mock.calls[0][1]).toBeDefined();
  });

  it('does not render download button', () => {
    const cbs = makeCallbacks();
    render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ installed: true })}
        activeModelId="some-other-model"
        provider="local"
        isDownloading={false}
        {...cbs}
      />,
    );
    expect(screen.queryByRole('button', { name: 'Download model' })).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Local + installed + id === activeModelId → no delete button
// ---------------------------------------------------------------------------

describe('local + installed + id === activeModelId', () => {
  it('does not render "Delete model" button when model is active', () => {
    const cbs = makeCallbacks();
    render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ id: 'nomic-embed-text', installed: true })}
        activeModelId="nomic-embed-text"
        provider="local"
        isDownloading={false}
        {...cbs}
      />,
    );
    expect(screen.queryByRole('button', { name: 'Delete model' })).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Active model Chip
// ---------------------------------------------------------------------------

describe('"Active" Chip', () => {
  it('renders "Active" chip when option.id === activeModelId', () => {
    const cbs = makeCallbacks();
    render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ id: 'nomic-embed-text', installed: true })}
        activeModelId="nomic-embed-text"
        provider="local"
        isDownloading={false}
        {...cbs}
      />,
    );
    expect(screen.getByText('Active')).toBeInTheDocument();
  });

  it('does NOT render "Active" chip when option.id !== activeModelId', () => {
    const cbs = makeCallbacks();
    render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ id: 'nomic-embed-text', installed: true })}
        activeModelId="mxbai-embed-large"
        provider="local"
        isDownloading={false}
        {...cbs}
      />,
    );
    expect(screen.queryByText('Active')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Description formatting
// ---------------------------------------------------------------------------

describe('description formatting', () => {
  it('shows "id · description" when description is present', () => {
    const cbs = makeCallbacks();
    render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ id: 'nomic-embed-text', description: 'A compact embedding model' })}
        activeModelId="other-model"
        provider="ollama"
        isDownloading={false}
        {...cbs}
      />,
    );
    expect(screen.getByText('nomic-embed-text · A compact embedding model')).toBeInTheDocument();
  });

  it('shows only the id when description is empty string', () => {
    const cbs = makeCallbacks();
    render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ id: 'nomic-embed-text', description: '' })}
        activeModelId="other-model"
        provider="ollama"
        isDownloading={false}
        {...cbs}
      />,
    );
    expect(screen.getByText('nomic-embed-text')).toBeInTheDocument();
    expect(screen.queryByText(/·/)).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Model name rendered
// ---------------------------------------------------------------------------

describe('model name', () => {
  it('renders the option name', () => {
    const cbs = makeCallbacks();
    render(
      <ModelOptionItem
        htmlProps={{}}
        option={makeOption({ name: 'Nomic Embed Text' })}
        activeModelId="other-model"
        provider="ollama"
        isDownloading={false}
        {...cbs}
      />,
    );
    expect(screen.getByText('Nomic Embed Text')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// htmlProps forwarding
// ---------------------------------------------------------------------------

describe('htmlProps forwarding', () => {
  it('forwards htmlProps (e.g., className) to the root li element', () => {
    const cbs = makeCallbacks();
    const { container } = render(
      <ModelOptionItem
        htmlProps={{ className: 'test-class-xyz', 'aria-selected': true } as React.HTMLAttributes<HTMLLIElement>}
        option={makeOption()}
        activeModelId="other-model"
        provider="ollama"
        isDownloading={false}
        {...cbs}
      />,
    );
    const li = container.querySelector('li');
    expect(li).toHaveClass('test-class-xyz');
  });
});
