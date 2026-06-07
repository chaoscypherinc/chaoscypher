// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for InstanceManager.tsx — Ollama instance CRUD + load balancing UI.
 *
 * Strategy: render the component directly (it is purely presentational — no
 * hooks, no network). Expand the MUI Accordion before asserting on
 * AccordionDetails content to avoid flakiness. All callbacks are vi.fn()
 * spies; assertions verify the exact arguments they are called with.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import InstanceManager from '../InstanceManager';
import type { Settings, OllamaInstance } from '../../../../types';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeSettings(
  overrides: Partial<Settings['llm']> = {},
): Settings {
  return {
    llm: {
      chat_provider: 'ollama',
      ollama_chat_model: 'llama3',
      ollama_instances: [],
      ...overrides,
    },
  } as unknown as Settings;
}

function makeInstance(overrides: Partial<OllamaInstance> = {}): OllamaInstance {
  return {
    id: 'inst-1',
    name: 'GPU Server 1',
    base_url: 'http://192.168.1.10:11434',
    enabled: true,
    healthy: true,
    last_health_check: null,
    last_error: null,
    ...overrides,
  };
}

// Default no-op callbacks — overridden per test where needed.
const defaultCallbacks = () => ({
  setSettings: vi.fn(),
  setNewInstance: vi.fn(),
  onAddInstance: vi.fn(),
  onRemoveInstance: vi.fn(),
  onToggleInstance: vi.fn(),
});

// ---------------------------------------------------------------------------
// Helper: expand the Accordion so AccordionDetails content is reachable.
// ---------------------------------------------------------------------------
function expandAccordion() {
  // The AccordionSummary contains the heading text.
  const summary = screen.getByText('Multiple Instances & Load Balancing');
  fireEvent.click(summary);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('InstanceManager — empty instances', () => {
  beforeEach(() => {
    const cbs = defaultCallbacks();
    render(
      <InstanceManager
        settings={makeSettings()}
        setSettings={cbs.setSettings}
        ollamaInstances={[]}
        enabledInstanceCount={0}
        newInstance={{ name: '', base_url: '' }}
        setNewInstance={cbs.setNewInstance}
        onAddInstance={cbs.onAddInstance}
        onRemoveInstance={cbs.onRemoveInstance}
        onToggleInstance={cbs.onToggleInstance}
      />,
    );
    expandAccordion();
  });

  it('shows "Disabled" chip in summary when there are no instances', () => {
    expect(screen.getByText('Disabled')).toBeInTheDocument();
  });

  it('renders the info Alert about distributing LLM requests', () => {
    expect(
      screen.getByText(/Distribute LLM requests across multiple Ollama instances/),
    ).toBeInTheDocument();
  });

  it('does NOT render the success Alert', () => {
    expect(screen.queryByText(/Load balancing enabled/)).not.toBeInTheDocument();
  });

  it('does NOT render the Load Balancing Strategy select', () => {
    expect(screen.queryByLabelText('Load Balancing Strategy')).not.toBeInTheDocument();
  });

  it('renders "Add a new Ollama instance:" prompt', () => {
    expect(screen.getByText('Add a new Ollama instance:')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------

describe('InstanceManager — with instances', () => {
  const instances = [
    makeInstance({ id: 'a', name: 'Alpha', base_url: 'http://alpha:11434', enabled: true, healthy: true }),
    makeInstance({ id: 'b', name: 'Beta', base_url: 'http://beta:11434', enabled: false, healthy: false, last_error: 'connection refused' }),
  ];

  beforeEach(() => {
    const cbs = defaultCallbacks();
    render(
      <InstanceManager
        settings={makeSettings()}
        setSettings={cbs.setSettings}
        ollamaInstances={instances}
        enabledInstanceCount={1}
        newInstance={{ name: '', base_url: '' }}
        setNewInstance={cbs.setNewInstance}
        onAddInstance={cbs.onAddInstance}
        onRemoveInstance={cbs.onRemoveInstance}
        onToggleInstance={cbs.onToggleInstance}
      />,
    );
    expandAccordion();
  });

  it('shows "1 active" chip in summary', () => {
    expect(screen.getByText('1 active')).toBeInTheDocument();
  });

  it('renders success Alert with singular "instance." for enabledInstanceCount=1', () => {
    expect(screen.getByText(/Load balancing enabled\. Requests distributed across 1 instance\./)).toBeInTheDocument();
  });

  it('renders both instance names and base_urls', () => {
    expect(screen.getByText('Alpha')).toBeInTheDocument();
    expect(screen.getByText('http://alpha:11434')).toBeInTheDocument();
    expect(screen.getByText('Beta')).toBeInTheDocument();
    expect(screen.getByText('http://beta:11434')).toBeInTheDocument();
  });

  it('does NOT render the info Alert when instances exist', () => {
    expect(screen.queryByText(/Distribute LLM requests/)).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------

describe('InstanceManager — plural instance count', () => {
  it('shows "N instances." (plural) when enabledInstanceCount > 1', () => {
    const instances = [
      makeInstance({ id: 'a' }),
      makeInstance({ id: 'b', name: 'Server B', base_url: 'http://b:11434' }),
      makeInstance({ id: 'c', name: 'Server C', base_url: 'http://c:11434' }),
    ];
    const cbs = defaultCallbacks();
    render(
      <InstanceManager
        settings={makeSettings()}
        setSettings={cbs.setSettings}
        ollamaInstances={instances}
        enabledInstanceCount={3}
        newInstance={{ name: '', base_url: '' }}
        setNewInstance={cbs.setNewInstance}
        onAddInstance={cbs.onAddInstance}
        onRemoveInstance={cbs.onRemoveInstance}
        onToggleInstance={cbs.onToggleInstance}
      />,
    );
    expandAccordion();
    expect(screen.getByText(/Load balancing enabled\. Requests distributed across 3 instances\./)).toBeInTheDocument();
  });

  it('shows "3 active" chip', () => {
    const instances = [
      makeInstance({ id: 'a' }),
      makeInstance({ id: 'b', name: 'Server B', base_url: 'http://b:11434' }),
      makeInstance({ id: 'c', name: 'Server C', base_url: 'http://c:11434' }),
    ];
    const cbs = defaultCallbacks();
    render(
      <InstanceManager
        settings={makeSettings()}
        setSettings={cbs.setSettings}
        ollamaInstances={instances}
        enabledInstanceCount={3}
        newInstance={{ name: '', base_url: '' }}
        setNewInstance={cbs.setNewInstance}
        onAddInstance={cbs.onAddInstance}
        onRemoveInstance={cbs.onRemoveInstance}
        onToggleInstance={cbs.onToggleInstance}
      />,
    );
    expect(screen.getByText('3 active')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------

describe('InstanceManager — Load Balancing Strategy Select', () => {
  const instances = [makeInstance()];

  it('renders the Load Balancing Strategy select when instances exist', () => {
    const cbs = defaultCallbacks();
    render(
      <InstanceManager
        settings={makeSettings({ ollama_load_balancing: undefined })}
        setSettings={cbs.setSettings}
        ollamaInstances={instances}
        enabledInstanceCount={1}
        newInstance={{ name: '', base_url: '' }}
        setNewInstance={cbs.setNewInstance}
        onAddInstance={cbs.onAddInstance}
        onRemoveInstance={cbs.onRemoveInstance}
        onToggleInstance={cbs.onToggleInstance}
      />,
    );
    expandAccordion();
    // The load balancing section is visible — combobox from MUI Select is present
    expect(screen.getByRole('combobox')).toBeInTheDocument();
    // The label text is present (may appear twice: once in InputLabel, once in Select value span)
    expect(screen.getAllByText('Load Balancing Strategy').length).toBeGreaterThan(0);
    // Default value shows "Round Robin"
    expect(screen.getByText('Round Robin')).toBeInTheDocument();
  });

  it('calls setSettings with least_loaded when Least Loaded is selected', () => {
    const setSettings = vi.fn();
    const settings = makeSettings({ ollama_load_balancing: 'round_robin' });
    render(
      <InstanceManager
        settings={settings}
        setSettings={setSettings}
        ollamaInstances={instances}
        enabledInstanceCount={1}
        newInstance={{ name: '', base_url: '' }}
        setNewInstance={vi.fn()}
        onAddInstance={vi.fn()}
        onRemoveInstance={vi.fn()}
        onToggleInstance={vi.fn()}
      />,
    );
    expandAccordion();

    // Trigger change on the MUI combobox element — MUI Select listens via
    // the hidden native input. We fire on the hidden input directly.
    const hiddenInput = document.querySelector('input[aria-hidden="true"]') as HTMLInputElement;
    fireEvent.change(hiddenInput, { target: { value: 'least_loaded' } });

    expect(setSettings).toHaveBeenCalledTimes(1);
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.llm.ollama_load_balancing).toBe('least_loaded');
  });

  it('calls setSettings with random when Random is selected', () => {
    const setSettings = vi.fn();
    const settings = makeSettings({ ollama_load_balancing: 'round_robin' });
    render(
      <InstanceManager
        settings={settings}
        setSettings={setSettings}
        ollamaInstances={instances}
        enabledInstanceCount={1}
        newInstance={{ name: '', base_url: '' }}
        setNewInstance={vi.fn()}
        onAddInstance={vi.fn()}
        onRemoveInstance={vi.fn()}
        onToggleInstance={vi.fn()}
      />,
    );
    expandAccordion();

    const hiddenInput = document.querySelector('input[aria-hidden="true"]') as HTMLInputElement;
    fireEvent.change(hiddenInput, { target: { value: 'random' } });

    expect(setSettings).toHaveBeenCalledTimes(1);
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.llm.ollama_load_balancing).toBe('random');
  });

  it('calls setSettings with round_robin when Round Robin is selected', () => {
    const setSettings = vi.fn();
    const settings = makeSettings({ ollama_load_balancing: 'random' });
    render(
      <InstanceManager
        settings={settings}
        setSettings={setSettings}
        ollamaInstances={instances}
        enabledInstanceCount={1}
        newInstance={{ name: '', base_url: '' }}
        setNewInstance={vi.fn()}
        onAddInstance={vi.fn()}
        onRemoveInstance={vi.fn()}
        onToggleInstance={vi.fn()}
      />,
    );
    expandAccordion();

    const hiddenInput = document.querySelector('input[aria-hidden="true"]') as HTMLInputElement;
    fireEvent.change(hiddenInput, { target: { value: 'round_robin' } });

    expect(setSettings).toHaveBeenCalledTimes(1);
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.llm.ollama_load_balancing).toBe('round_robin');
  });
});

// ---------------------------------------------------------------------------

describe('InstanceManager — instance row interactions', () => {
  const healthyInstance = makeInstance({ id: 'h1', name: 'Healthy', base_url: 'http://healthy:11434', healthy: true, enabled: true });
  const unhealthyInstance = makeInstance({ id: 'u1', name: 'Sick', base_url: 'http://sick:11434', healthy: false, enabled: true, last_error: 'timeout' });

  it('healthy instance shows CheckCircleIcon (data-testid=CheckCircleIcon)', () => {
    const cbs = defaultCallbacks();
    render(
      <InstanceManager
        settings={makeSettings()}
        setSettings={cbs.setSettings}
        ollamaInstances={[healthyInstance]}
        enabledInstanceCount={1}
        newInstance={{ name: '', base_url: '' }}
        setNewInstance={cbs.setNewInstance}
        onAddInstance={cbs.onAddInstance}
        onRemoveInstance={cbs.onRemoveInstance}
        onToggleInstance={cbs.onToggleInstance}
      />,
    );
    expandAccordion();
    // MUI icons ship with data-testid equal to their component name in tests
    expect(screen.getByTestId('CheckCircleIcon')).toBeInTheDocument();
    // ErrorIcon should NOT be present for a healthy instance
    expect(screen.queryByTestId('ErrorIcon')).not.toBeInTheDocument();
  });

  it('unhealthy instance shows ErrorIcon (data-testid=ErrorIcon)', () => {
    const cbs = defaultCallbacks();
    render(
      <InstanceManager
        settings={makeSettings()}
        setSettings={cbs.setSettings}
        ollamaInstances={[unhealthyInstance]}
        enabledInstanceCount={1}
        newInstance={{ name: '', base_url: '' }}
        setNewInstance={cbs.setNewInstance}
        onAddInstance={cbs.onAddInstance}
        onRemoveInstance={cbs.onRemoveInstance}
        onToggleInstance={cbs.onToggleInstance}
      />,
    );
    expandAccordion();
    expect(screen.getByTestId('ErrorIcon')).toBeInTheDocument();
    // CheckCircleIcon should NOT be present for an unhealthy instance
    expect(screen.queryByTestId('CheckCircleIcon')).not.toBeInTheDocument();
  });

  it('both healthy and unhealthy instances render their respective icons', () => {
    const instance = makeInstance({ id: 'u2', name: 'Dead Server', base_url: 'http://dead:11434', healthy: false, last_error: null });
    const cbs = defaultCallbacks();
    render(
      <InstanceManager
        settings={makeSettings()}
        setSettings={cbs.setSettings}
        ollamaInstances={[healthyInstance, instance]}
        enabledInstanceCount={1}
        newInstance={{ name: '', base_url: '' }}
        setNewInstance={cbs.setNewInstance}
        onAddInstance={cbs.onAddInstance}
        onRemoveInstance={cbs.onRemoveInstance}
        onToggleInstance={cbs.onToggleInstance}
      />,
    );
    expandAccordion();
    expect(screen.getByTestId('CheckCircleIcon')).toBeInTheDocument();
    expect(screen.getByTestId('ErrorIcon')).toBeInTheDocument();
  });

  it('clicking the Switch calls onToggleInstance with the instance id', () => {
    const onToggleInstance = vi.fn();
    render(
      <InstanceManager
        settings={makeSettings()}
        setSettings={vi.fn()}
        ollamaInstances={[healthyInstance]}
        enabledInstanceCount={1}
        newInstance={{ name: '', base_url: '' }}
        setNewInstance={vi.fn()}
        onAddInstance={vi.fn()}
        onRemoveInstance={vi.fn()}
        onToggleInstance={onToggleInstance}
      />,
    );
    expandAccordion();

    // MUI Switch renders an input[type=checkbox] inside the Collapse; we query
    // it directly because MUI Collapse keeps it hidden (visibility:hidden) until
    // the CSS transition completes (no-op in jsdom), so getByRole('checkbox')
    // would miss it without { hidden: true }.
    const switchInput = document.querySelector('input[type="checkbox"]') as HTMLInputElement;
    expect(switchInput).not.toBeNull();
    fireEvent.click(switchInput);

    expect(onToggleInstance).toHaveBeenCalledTimes(1);
    expect(onToggleInstance).toHaveBeenCalledWith('h1');
  });

  it('clicking Delete IconButton calls onRemoveInstance with instance id', () => {
    const onRemoveInstance = vi.fn();
    render(
      <InstanceManager
        settings={makeSettings()}
        setSettings={vi.fn()}
        ollamaInstances={[healthyInstance]}
        enabledInstanceCount={1}
        newInstance={{ name: '', base_url: '' }}
        setNewInstance={vi.fn()}
        onAddInstance={vi.fn()}
        onRemoveInstance={onRemoveInstance}
        onToggleInstance={vi.fn()}
      />,
    );
    expandAccordion();

    const deleteBtn = screen.getByRole('button', { name: 'Delete instance' });
    fireEvent.click(deleteBtn);

    expect(onRemoveInstance).toHaveBeenCalledTimes(1);
    expect(onRemoveInstance).toHaveBeenCalledWith('h1');
  });

  it('renders multiple instance rows with correct toggle/delete dispatch', () => {
    const inst1 = makeInstance({ id: 'x1', name: 'X1', base_url: 'http://x1:11434' });
    const inst2 = makeInstance({ id: 'x2', name: 'X2', base_url: 'http://x2:11434' });
    const onToggleInstance = vi.fn();
    const onRemoveInstance = vi.fn();
    render(
      <InstanceManager
        settings={makeSettings()}
        setSettings={vi.fn()}
        ollamaInstances={[inst1, inst2]}
        enabledInstanceCount={2}
        newInstance={{ name: '', base_url: '' }}
        setNewInstance={vi.fn()}
        onAddInstance={vi.fn()}
        onRemoveInstance={onRemoveInstance}
        onToggleInstance={onToggleInstance}
      />,
    );
    expandAccordion();

    const deleteButtons = screen.getAllByRole('button', { name: 'Delete instance' });
    expect(deleteButtons).toHaveLength(2);
    fireEvent.click(deleteButtons[1]);
    expect(onRemoveInstance).toHaveBeenCalledWith('x2');

    // MUI Switch input[type=checkbox] — query directly (see comment in sibling test)
    const switches = document.querySelectorAll('input[type="checkbox"]');
    expect(switches).toHaveLength(2);
    fireEvent.click(switches[0]);
    expect(onToggleInstance).toHaveBeenCalledWith('x1');
  });
});

// ---------------------------------------------------------------------------

describe('InstanceManager — Add New Instance form', () => {
  it('Name TextField onChange calls setNewInstance with updated name', async () => {
    const setNewInstance = vi.fn();
    render(
      <InstanceManager
        settings={makeSettings()}
        setSettings={vi.fn()}
        ollamaInstances={[]}
        enabledInstanceCount={0}
        newInstance={{ name: '', base_url: '' }}
        setNewInstance={setNewInstance}
        onAddInstance={vi.fn()}
        onRemoveInstance={vi.fn()}
        onToggleInstance={vi.fn()}
      />,
    );
    expandAccordion();

    const nameField = screen.getByLabelText('Name');
    await userEvent.type(nameField, 'A');

    // setNewInstance called with the spread of existing value + new name
    expect(setNewInstance).toHaveBeenCalledWith({ name: 'A', base_url: '' });
  });

  it('Base URL TextField onChange calls setNewInstance with updated base_url', async () => {
    const setNewInstance = vi.fn();
    render(
      <InstanceManager
        settings={makeSettings()}
        setSettings={vi.fn()}
        ollamaInstances={[]}
        enabledInstanceCount={0}
        newInstance={{ name: 'My Server', base_url: '' }}
        setNewInstance={setNewInstance}
        onAddInstance={vi.fn()}
        onRemoveInstance={vi.fn()}
        onToggleInstance={vi.fn()}
      />,
    );
    expandAccordion();

    const urlField = screen.getByLabelText('Base URL');
    await userEvent.type(urlField, 'h');

    expect(setNewInstance).toHaveBeenCalledWith({ name: 'My Server', base_url: 'h' });
  });

  it('Add button is disabled when name is empty', () => {
    render(
      <InstanceManager
        settings={makeSettings()}
        setSettings={vi.fn()}
        ollamaInstances={[]}
        enabledInstanceCount={0}
        newInstance={{ name: '', base_url: 'http://x:11434' }}
        setNewInstance={vi.fn()}
        onAddInstance={vi.fn()}
        onRemoveInstance={vi.fn()}
        onToggleInstance={vi.fn()}
      />,
    );
    expandAccordion();

    const addBtn = screen.getByRole('button', { name: /Add/i });
    expect(addBtn).toBeDisabled();
  });

  it('Add button is disabled when base_url is empty', () => {
    render(
      <InstanceManager
        settings={makeSettings()}
        setSettings={vi.fn()}
        ollamaInstances={[]}
        enabledInstanceCount={0}
        newInstance={{ name: 'Server A', base_url: '' }}
        setNewInstance={vi.fn()}
        onAddInstance={vi.fn()}
        onRemoveInstance={vi.fn()}
        onToggleInstance={vi.fn()}
      />,
    );
    expandAccordion();

    const addBtn = screen.getByRole('button', { name: /Add/i });
    expect(addBtn).toBeDisabled();
  });

  it('Add button is disabled when name is only whitespace', () => {
    render(
      <InstanceManager
        settings={makeSettings()}
        setSettings={vi.fn()}
        ollamaInstances={[]}
        enabledInstanceCount={0}
        newInstance={{ name: '   ', base_url: 'http://x:11434' }}
        setNewInstance={vi.fn()}
        onAddInstance={vi.fn()}
        onRemoveInstance={vi.fn()}
        onToggleInstance={vi.fn()}
      />,
    );
    expandAccordion();

    const addBtn = screen.getByRole('button', { name: /Add/i });
    expect(addBtn).toBeDisabled();
  });

  it('Add button is disabled when base_url is only whitespace', () => {
    render(
      <InstanceManager
        settings={makeSettings()}
        setSettings={vi.fn()}
        ollamaInstances={[]}
        enabledInstanceCount={0}
        newInstance={{ name: 'Server A', base_url: '   ' }}
        setNewInstance={vi.fn()}
        onAddInstance={vi.fn()}
        onRemoveInstance={vi.fn()}
        onToggleInstance={vi.fn()}
      />,
    );
    expandAccordion();

    const addBtn = screen.getByRole('button', { name: /Add/i });
    expect(addBtn).toBeDisabled();
  });

  it('Add button is enabled when both name and base_url are non-empty', () => {
    render(
      <InstanceManager
        settings={makeSettings()}
        setSettings={vi.fn()}
        ollamaInstances={[]}
        enabledInstanceCount={0}
        newInstance={{ name: 'Server A', base_url: 'http://x:11434' }}
        setNewInstance={vi.fn()}
        onAddInstance={vi.fn()}
        onRemoveInstance={vi.fn()}
        onToggleInstance={vi.fn()}
      />,
    );
    expandAccordion();

    const addBtn = screen.getByRole('button', { name: /Add/i });
    expect(addBtn).not.toBeDisabled();
  });

  it('clicking enabled Add button calls onAddInstance()', () => {
    const onAddInstance = vi.fn();
    render(
      <InstanceManager
        settings={makeSettings()}
        setSettings={vi.fn()}
        ollamaInstances={[]}
        enabledInstanceCount={0}
        newInstance={{ name: 'Server A', base_url: 'http://x:11434' }}
        setNewInstance={vi.fn()}
        onAddInstance={onAddInstance}
        onRemoveInstance={vi.fn()}
        onToggleInstance={vi.fn()}
      />,
    );
    expandAccordion();

    const addBtn = screen.getByRole('button', { name: /Add/i });
    fireEvent.click(addBtn);

    expect(onAddInstance).toHaveBeenCalledTimes(1);
  });
});
