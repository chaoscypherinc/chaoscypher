// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';

import VRAMPresets from '../VRAMPresets';
import type { Settings } from '../../../../types';

/**
 * Build a Settings object populated only with the LLM fields VRAMPresets touches.
 * Other top-level keys are not read by the component, so we cast through unknown.
 */
function makeSettings(overrides?: Partial<Settings['llm']>): Settings {
  return {
    llm: {
      ollama_num_batch: undefined,
      ollama_num_parallel: undefined,
      ollama_num_thread: undefined,
      ai_temperature: 0.7,
      ai_max_tokens: 2048,
      enable_token_cost_tracking: false,
      token_cost_input_per_million: 0,
      token_cost_output_per_million: 0,
      llm_max_concurrent: 1,
      llm_reserved_interactive: 0,
      llm_max_retries: 3,
      ...overrides,
    },
  } as unknown as Settings;
}

/** Find the numeric input under a label. MUI wraps inputs, so we go via the label. */
function getNumberInput(label: RegExp | string): HTMLInputElement {
  return screen.getByLabelText(label) as HTMLInputElement;
}

describe('VRAMPresets', () => {
  const setSettings = vi.fn();

  beforeEach(() => {
    setSettings.mockClear();
  });

  it('renders all three accordion section titles', () => {
    render(<VRAMPresets settings={makeSettings()} setSettings={setSettings} />);
    expect(screen.getByText('Performance Tuning')).toBeInTheDocument();
    expect(screen.getByText('General LLM Settings')).toBeInTheDocument();
    expect(screen.getByText('Queue & Priority Settings')).toBeInTheDocument();
  });

  it('renders empty string for unset Ollama numeric fields', () => {
    render(<VRAMPresets settings={makeSettings()} setSettings={setSettings} />);
    expect(getNumberInput(/Batch Size/i).value).toBe('');
    expect(getNumberInput(/Parallel Sequences/i).value).toBe('');
    expect(getNumberInput(/CPU Threads/i).value).toBe('');
  });

  it('renders provided Ollama numeric fields', () => {
    render(
      <VRAMPresets
        settings={makeSettings({
          ollama_num_batch: 512,
          ollama_num_parallel: 4,
          ollama_num_thread: 8,
        })}
        setSettings={setSettings}
      />,
    );
    expect(getNumberInput(/Batch Size/i).value).toBe('512');
    expect(getNumberInput(/Parallel Sequences/i).value).toBe('4');
    expect(getNumberInput(/CPU Threads/i).value).toBe('8');
  });

  it('parses Ollama batch size on change', () => {
    const settings = makeSettings();
    render(<VRAMPresets settings={settings} setSettings={setSettings} />);
    fireEvent.change(getNumberInput(/Batch Size/i), { target: { value: '256' } });
    expect(setSettings).toHaveBeenCalledTimes(1);
    expect(setSettings.mock.calls[0][0].llm.ollama_num_batch).toBe(256);
  });

  it('clears Ollama batch size to undefined when emptied', () => {
    const settings = makeSettings({ ollama_num_batch: 512 });
    render(<VRAMPresets settings={settings} setSettings={setSettings} />);
    fireEvent.change(getNumberInput(/Batch Size/i), { target: { value: '' } });
    expect(setSettings).toHaveBeenCalledTimes(1);
    expect(setSettings.mock.calls[0][0].llm.ollama_num_batch).toBeUndefined();
  });

  it('parses Ollama num_parallel and num_thread on change', () => {
    render(<VRAMPresets settings={makeSettings()} setSettings={setSettings} />);
    fireEvent.change(getNumberInput(/Parallel Sequences/i), { target: { value: '2' } });
    fireEvent.change(getNumberInput(/CPU Threads/i), { target: { value: '16' } });
    expect(setSettings).toHaveBeenCalledTimes(2);
    expect(setSettings.mock.calls[0][0].llm.ollama_num_parallel).toBe(2);
    expect(setSettings.mock.calls[1][0].llm.ollama_num_thread).toBe(16);
  });

  it('does NOT render token-cost inputs when tracking is disabled', () => {
    render(
      <VRAMPresets
        settings={makeSettings({ enable_token_cost_tracking: false })}
        setSettings={setSettings}
      />,
    );
    expect(screen.queryByLabelText(/Input \$/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Output \$/i)).not.toBeInTheDocument();
  });

  it('renders token-cost inputs when tracking is enabled', () => {
    render(
      <VRAMPresets
        settings={makeSettings({
          enable_token_cost_tracking: true,
          token_cost_input_per_million: 1.5,
          token_cost_output_per_million: 2.5,
        })}
        setSettings={setSettings}
      />,
    );
    expect(getNumberInput(/Input \$/i).value).toBe('1.5');
    expect(getNumberInput(/Output \$/i).value).toBe('2.5');
  });

  it('toggles cost-tracking checkbox', () => {
    render(
      <VRAMPresets
        settings={makeSettings({ enable_token_cost_tracking: false })}
        setSettings={setSettings}
      />,
    );
    // MUI's Checkbox renders an <input type="checkbox"> with no accessible name
    // (the FormControlLabel label is a sibling, not a `for=`-linked label).
    // Grab it via the hidden-but-present DOM input.
    const checkbox = document.querySelector(
      'input[type="checkbox"]',
    ) as HTMLInputElement;
    expect(checkbox).not.toBeNull();
    fireEvent.click(checkbox);
    expect(setSettings).toHaveBeenCalledTimes(1);
    expect(setSettings.mock.calls[0][0].llm.enable_token_cost_tracking).toBe(true);
  });

  it('parses token cost input and clamps NaN to 0', () => {
    render(
      <VRAMPresets
        settings={makeSettings({
          enable_token_cost_tracking: true,
          token_cost_input_per_million: 1.0,
          token_cost_output_per_million: 0,
        })}
        setSettings={setSettings}
      />,
    );
    fireEvent.change(getNumberInput(/Input \$/i), { target: { value: '3.75' } });
    expect(setSettings.mock.calls[0][0].llm.token_cost_input_per_million).toBe(3.75);

    fireEvent.change(getNumberInput(/Output \$/i), { target: { value: 'not-a-number' } });
    // parseFloat('not-a-number') === NaN, falsey, fallback is 0.
    expect(setSettings.mock.calls[1][0].llm.token_cost_output_per_million).toBe(0);
  });

  it('parses Temperature and Max Tokens', () => {
    render(<VRAMPresets settings={makeSettings()} setSettings={setSettings} />);
    fireEvent.change(getNumberInput(/Temperature/i), { target: { value: '1.2' } });
    fireEvent.change(getNumberInput(/Max Tokens \(Output\)/i), { target: { value: '4096' } });
    expect(setSettings.mock.calls[0][0].llm.ai_temperature).toBe(1.2);
    expect(setSettings.mock.calls[1][0].llm.ai_max_tokens).toBe(4096);
  });

  it('clamps reserved-interactive to (max_concurrent - 1) when max shrinks', () => {
    // Start with concurrent=4 + reserved=3, then drop concurrent to 2 → reserved must clamp to 1.
    render(
      <VRAMPresets
        settings={makeSettings({ llm_max_concurrent: 4, llm_reserved_interactive: 3 })}
        setSettings={setSettings}
      />,
    );
    fireEvent.change(getNumberInput(/Max Concurrent LLM Requests/i), { target: { value: '2' } });
    expect(setSettings).toHaveBeenCalledTimes(1);
    const next = setSettings.mock.calls[0][0].llm;
    expect(next.llm_max_concurrent).toBe(2);
    expect(next.llm_reserved_interactive).toBe(1);
  });

  it('floors reserved-interactive at 0 when max_concurrent drops to 1', () => {
    render(
      <VRAMPresets
        settings={makeSettings({ llm_max_concurrent: 4, llm_reserved_interactive: 2 })}
        setSettings={setSettings}
      />,
    );
    fireEvent.change(getNumberInput(/Max Concurrent LLM Requests/i), { target: { value: '1' } });
    const next = setSettings.mock.calls[0][0].llm;
    expect(next.llm_max_concurrent).toBe(1);
    // reserved becomes min(2, 0) = 0, then max(0, 0) = 0
    expect(next.llm_reserved_interactive).toBe(0);
  });

  it('clamps reserved-interactive entry to (max - 1)', () => {
    render(
      <VRAMPresets
        settings={makeSettings({ llm_max_concurrent: 3, llm_reserved_interactive: 0 })}
        setSettings={setSettings}
      />,
    );
    // try to set reserved=10 with max=3 → must clamp to 2
    fireEvent.change(getNumberInput(/Reserved Slots for Interactive Chat/i), {
      target: { value: '10' },
    });
    expect(setSettings.mock.calls[0][0].llm.llm_reserved_interactive).toBe(2);
  });

  it('renders dynamic helper text reflecting current max', () => {
    render(
      <VRAMPresets
        settings={makeSettings({ llm_max_concurrent: 5 })}
        setSettings={setSettings}
      />,
    );
    // helperText reads `Reserved for chat (max 4)` when concurrent=5
    expect(screen.getByText(/Reserved for chat \(max 4\)/i)).toBeInTheDocument();
  });

  it('shows max 0 helper text when concurrent is 1', () => {
    render(
      <VRAMPresets
        settings={makeSettings({ llm_max_concurrent: 1 })}
        setSettings={setSettings}
      />,
    );
    expect(screen.getByText(/Reserved for chat \(max 0\)/i)).toBeInTheDocument();
  });

  it('parses Max Retries on change', () => {
    render(<VRAMPresets settings={makeSettings()} setSettings={setSettings} />);
    fireEvent.change(getNumberInput(/Max Retries/i), { target: { value: '7' } });
    expect(setSettings.mock.calls[0][0].llm.llm_max_retries).toBe(7);
  });

  it('falls back to default 3 for Max Retries when input is non-numeric', () => {
    render(<VRAMPresets settings={makeSettings()} setSettings={setSettings} />);
    fireEvent.change(getNumberInput(/Max Retries/i), { target: { value: 'abc' } });
    // parseInt('abc') === NaN, fallback 3
    expect(setSettings.mock.calls[0][0].llm.llm_max_retries).toBe(3);
  });

  it('renders the Ollama modelfile docs link', () => {
    const { container } = render(
      <VRAMPresets settings={makeSettings()} setSettings={setSettings} />,
    );
    // <Link> is rendered as <a> but the accessible-name query is brittle here;
    // querying by href avoids depending on text-vs-tree role detection.
    const link = container.querySelector(
      'a[href*="ollama/ollama"]',
    ) as HTMLAnchorElement | null;
    expect(link).not.toBeNull();
    expect(link!.getAttribute('target')).toBe('_blank');
  });

  it('renders all three info Alert banners', () => {
    const { container } = render(
      <VRAMPresets settings={makeSettings()} setSettings={setSettings} />,
    );
    // MUI v9 Alert root carries the `MuiAlert-root` class; severity="info"
    // adds `MuiAlert-standardInfo`. We assert all three present.
    const alerts = within(container as HTMLElement).queryAllByText(
      /Advanced Ollama parameters|Configure AI behavior|how LLM requests are prioritized/i,
    );
    expect(alerts.length).toBe(3);
  });
});
