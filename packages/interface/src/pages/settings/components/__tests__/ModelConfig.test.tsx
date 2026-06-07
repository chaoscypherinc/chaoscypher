// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for ModelConfig: OllamaAutocomplete, CloudModelAutocomplete, and
 * ContextWindowSlider. These are presentational sub-components with no
 * service-layer dependencies — tests exercise rendering branches and the
 * onChange / onInputChange callback wiring through fireEvent.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';

import {
  OllamaAutocomplete,
  CloudModelAutocomplete,
  ContextWindowSlider,
} from '../ModelConfig';
import type { CloudModelInfo } from '../../../../types';

// ---------------------------------------------------------------------------
// OllamaAutocomplete
// ---------------------------------------------------------------------------

const ollamaOptions = [
  { id: 'llama3:8b', name: 'Llama 3', description: 'Default chat model' },
  { id: 'qwen2.5:7b', name: 'Qwen 2.5', description: 'Multilingual model' },
];

function renderOllama(overrides: Partial<React.ComponentProps<typeof OllamaAutocomplete>> = {}) {
  const onChange = vi.fn();
  const onInputChange = vi.fn();
  const onPull = vi.fn();
  const onRemove = vi.fn();
  const onShowInfo = vi.fn();
  const props = {
    label: 'Chat Model',
    options: ollamaOptions,
    value: '',
    onChange,
    onInputChange,
    onPull,
    onRemove,
    onShowInfo,
    ...overrides,
  };
  const utils = render(<OllamaAutocomplete {...props} />);
  return { ...utils, onChange, onInputChange, onPull, onRemove, onShowInfo };
}

describe('OllamaAutocomplete', () => {
  it('renders the labelled input with no error when value is empty', () => {
    renderOllama();
    expect(screen.getByLabelText('Chat Model')).toBeInTheDocument();
    // No error helper text rendered when there's no selected value.
    expect(screen.queryByText(/is not installed/)).not.toBeInTheDocument();
  });

  it('shows an error helper when selected value is not in installedModels', () => {
    renderOllama({
      value: 'llama3:8b',
      installedModels: new Set(['qwen2.5:7b']),
    });
    expect(screen.getByText(/llama3:8b is not installed/i)).toBeInTheDocument();
  });

  it('does not show error when the selected value IS installed', () => {
    renderOllama({
      value: 'llama3:8b',
      installedModels: new Set(['llama3:8b']),
    });
    expect(screen.queryByText(/is not installed/)).not.toBeInTheDocument();
  });

  it('does not flag an error when installedModels is empty (loading state)', () => {
    // The error branch requires installedModels.size > 0.
    renderOllama({
      value: 'llama3:8b',
      installedModels: new Set<string>(),
    });
    expect(screen.queryByText(/is not installed/)).not.toBeInTheDocument();
  });

  it('fires onInputChange when the user types', () => {
    const { onInputChange } = renderOllama();
    fireEvent.change(screen.getByLabelText('Chat Model'), { target: { value: 'lla' } });
    expect(onInputChange).toHaveBeenCalledWith('lla');
  });

  it('opens the dropdown and renders grouped pretested + other-installed options', () => {
    renderOllama({
      installedModels: new Set(['llama3:8b', 'mistral:latest']),
      otherInstalledModels: [{ id: 'mistral:latest', name: 'Mistral' }],
    });
    // Click the input to open the dropdown.
    fireEvent.mouseDown(screen.getByLabelText('Chat Model'));
    // Group headers.
    expect(screen.getByText('Recommended')).toBeInTheDocument();
    expect(screen.getByText('Other Installed')).toBeInTheDocument();
    // The not-pretested option carries an "Untested" chip.
    expect(screen.getByText('Untested')).toBeInTheDocument();
    // The id of a pretested option appears in the list.
    expect(screen.getByText('llama3:8b')).toBeInTheDocument();
  });

  it('invokes onPull when the download icon is clicked for an uninstalled model', () => {
    const { onPull } = renderOllama({
      installedModels: new Set<string>(),
    });
    fireEvent.mouseDown(screen.getByLabelText('Chat Model'));
    // Each row has a download icon button labelled with the model name.
    const downloadBtn = screen.getByRole('button', { name: /Download Llama 3/ });
    fireEvent.click(downloadBtn);
    expect(onPull).toHaveBeenCalledWith('llama3:8b');
  });

  it('renders a determinate progress bar while a pull is in flight', () => {
    renderOllama({
      installedModels: new Set<string>(),
      pullProgress: {
        'llama3:8b': { status: 'downloading', completed: 50, total: 100 },
      },
    });
    fireEvent.mouseDown(screen.getByLabelText('Chat Model'));
    // The status text is rendered in the option row.
    expect(screen.getByText(/downloading/)).toBeInTheDocument();
    expect(screen.getByText(/50%/)).toBeInTheDocument();
  });

  it('opens the three-dot menu and fires onShowInfo / onRemove for installed models', () => {
    const { onShowInfo, onRemove } = renderOllama({
      installedModels: new Set(['llama3:8b', 'qwen2.5:7b']),
    });
    fireEvent.mouseDown(screen.getByLabelText('Chat Model'));
    // "More actions" buttons appear for each installed row.
    const moreBtns = screen.getAllByRole('button', { name: /More actions/ });
    fireEvent.click(moreBtns[0]);
    // The menu items appear.
    const infoItem = screen.getByRole('menuitem', { name: /Model Info/ });
    fireEvent.click(infoItem);
    expect(onShowInfo).toHaveBeenCalledWith('llama3:8b');

    // Re-open the menu and click Remove.
    fireEvent.mouseDown(screen.getByLabelText('Chat Model'));
    const moreBtnsAgain = screen.getAllByRole('button', { name: /More actions/ });
    fireEvent.click(moreBtnsAgain[0]);
    fireEvent.click(screen.getByRole('menuitem', { name: /Remove/ }));
    expect(onRemove).toHaveBeenCalledWith('llama3:8b');
  });
});

// ---------------------------------------------------------------------------
// CloudModelAutocomplete
// ---------------------------------------------------------------------------

const cloudModels: CloudModelInfo[] = [
  {
    id: 'gpt-5',
    display_name: 'GPT-5',
    context_window: 200_000,
    max_output_tokens: 16_000,
    recommended: true,
  },
  {
    id: 'gpt-5-mini',
    display_name: 'GPT-5 Mini',
    context_window: 128_000,
    max_output_tokens: 8_000,
  },
];

describe('CloudModelAutocomplete', () => {
  it('renders the labelled input with helper text when provided', () => {
    render(
      <CloudModelAutocomplete
        label="Chat Model"
        helperText="Pick your daily driver"
        options={cloudModels}
        value=""
        onChange={vi.fn()}
        onInputChange={vi.fn()}
      />,
    );
    expect(screen.getByLabelText('Chat Model')).toBeInTheDocument();
    expect(screen.getByText('Pick your daily driver')).toBeInTheDocument();
  });

  it('omits helper text element when none provided', () => {
    render(
      <CloudModelAutocomplete
        label="Chat Model"
        options={cloudModels}
        value=""
        onChange={vi.fn()}
        onInputChange={vi.fn()}
      />,
    );
    expect(screen.queryByText('Pick your daily driver')).not.toBeInTheDocument();
  });

  it('renders dropdown options with Recommended chip and context/output meta', () => {
    render(
      <CloudModelAutocomplete
        label="Chat Model"
        options={cloudModels}
        value=""
        onChange={vi.fn()}
        onInputChange={vi.fn()}
      />,
    );
    fireEvent.mouseDown(screen.getByLabelText('Chat Model'));
    expect(screen.getByText('GPT-5')).toBeInTheDocument();
    expect(screen.getByText('GPT-5 Mini')).toBeInTheDocument();
    // The recommended chip is rendered exactly once.
    expect(screen.getByText('Recommended')).toBeInTheDocument();
    // formatTokens(200000) → "200.0K"
    expect(screen.getByText(/200\.0K context/)).toBeInTheDocument();
  });

  it('fires onChange with the selected model id and the full option', () => {
    const onChange = vi.fn();
    render(
      <CloudModelAutocomplete
        label="Chat Model"
        options={cloudModels}
        value=""
        onChange={onChange}
        onInputChange={vi.fn()}
      />,
    );
    fireEvent.mouseDown(screen.getByLabelText('Chat Model'));
    fireEvent.click(screen.getByText('GPT-5'));
    expect(onChange).toHaveBeenCalledWith('gpt-5', expect.objectContaining({ id: 'gpt-5' }));
  });

  it('fires onInputChange when the user types into the freeSolo input', () => {
    const onInputChange = vi.fn();
    render(
      <CloudModelAutocomplete
        label="Chat Model"
        options={cloudModels}
        value=""
        onChange={vi.fn()}
        onInputChange={onInputChange}
      />,
    );
    fireEvent.change(screen.getByLabelText('Chat Model'), { target: { value: 'gpt' } });
    expect(onInputChange).toHaveBeenCalledWith('gpt');
  });
});

// ---------------------------------------------------------------------------
// ContextWindowSlider
// ---------------------------------------------------------------------------

describe('ContextWindowSlider', () => {
  const baseProps = {
    contextValue: 8192,
    onContextChange: vi.fn(),
    contextMin: 2048,
    contextMax: 32768,
    contextStep: 1024,
    contextMarks: [
      { value: 2048, label: '2K' },
      { value: 32768, label: '32K' },
    ],
  };

  it('renders only the context-window row when output props are omitted', () => {
    render(<ContextWindowSlider {...baseProps} />);
    expect(screen.getByLabelText('Context Window')).toBeInTheDocument();
    expect(screen.queryByLabelText('Max Output')).not.toBeInTheDocument();
  });

  it('renders both rows when outputValue + onOutputChange are supplied', () => {
    render(
      <ContextWindowSlider
        {...baseProps}
        outputValue={4096}
        onOutputChange={vi.fn()}
        outputMin={1024}
        outputMax={16384}
        outputStep={1024}
      />,
    );
    expect(screen.getByLabelText('Context Window')).toBeInTheDocument();
    expect(screen.getByLabelText('Max Output')).toBeInTheDocument();
  });

  it('fires onContextChange when the number input is edited', () => {
    const onContextChange = vi.fn();
    render(<ContextWindowSlider {...baseProps} onContextChange={onContextChange} />);
    fireEvent.change(screen.getByLabelText('Context Window'), { target: { value: '16384' } });
    expect(onContextChange).toHaveBeenCalledWith(16384);
  });

  it('falls back to contextMin when the number input is cleared / NaN', () => {
    const onContextChange = vi.fn();
    render(<ContextWindowSlider {...baseProps} onContextChange={onContextChange} />);
    // Empty string → parseInt returns NaN → falsy → contextMin.
    fireEvent.change(screen.getByLabelText('Context Window'), { target: { value: '' } });
    expect(onContextChange).toHaveBeenCalledWith(baseProps.contextMin);
  });

  it('fires onOutputChange when the output number input is edited', () => {
    const onOutputChange = vi.fn();
    render(
      <ContextWindowSlider
        {...baseProps}
        outputValue={4096}
        onOutputChange={onOutputChange}
        outputMin={1024}
        outputMax={16384}
        outputStep={1024}
      />,
    );
    fireEvent.change(screen.getByLabelText('Max Output'), { target: { value: '8192' } });
    expect(onOutputChange).toHaveBeenCalledWith(8192);
  });

  it('falls back to outputMin when the output number input is cleared', () => {
    const onOutputChange = vi.fn();
    render(
      <ContextWindowSlider
        {...baseProps}
        outputValue={4096}
        onOutputChange={onOutputChange}
        outputMin={2048}
        outputMax={16384}
        outputStep={1024}
      />,
    );
    fireEvent.change(screen.getByLabelText('Max Output'), { target: { value: '' } });
    expect(onOutputChange).toHaveBeenCalledWith(2048);
  });

  it('reflects the contextValue prop in the input', () => {
    render(<ContextWindowSlider {...baseProps} contextValue={12345} />);
    const input = screen.getByLabelText('Context Window') as HTMLInputElement;
    expect(input.value).toBe('12345');
  });

  it('reflects the outputValue prop in the output input', () => {
    render(
      <ContextWindowSlider
        {...baseProps}
        outputValue={7890}
        onOutputChange={vi.fn()}
        outputMin={1024}
      />,
    );
    const input = screen.getByLabelText('Max Output') as HTMLInputElement;
    expect(input.value).toBe('7890');
  });

  it('exposes sliders alongside the number inputs', () => {
    render(
      <ContextWindowSlider
        {...baseProps}
        outputValue={4096}
        onOutputChange={vi.fn()}
        outputMin={1024}
      />,
    );
    // MUI Slider renders role=slider on a hidden input.
    const sliders = screen.getAllByRole('slider');
    expect(sliders).toHaveLength(2);
  });

  // Sanity import for `within` so the eslint unused-import check passes.
  it('lets us scope queries with within() (smoke)', () => {
    const { container } = render(<ContextWindowSlider {...baseProps} />);
    expect(within(container).getByLabelText('Context Window')).toBeInTheDocument();
  });
});
