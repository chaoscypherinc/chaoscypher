// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for NodePropertiesForm — the props-driven editable form for a
 * selected graph node. Covers title editing, metadata tooltip + copy,
 * template-driven property fields, the read-only "facts" list with inline
 * editing, tags, connected nodes (real graphology graph), provenance, and
 * the save/delete actions.
 */

import { useState } from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material';
import Graph from 'graphology';
import NodePropertiesForm from '../NodePropertiesForm';
import type { GraphNodeData, NodeAttributes, EdgeAttributes } from '../../types';
import type { SourceGroupState } from '../../hooks/useSourceGroups';
import type { Template, PropertyDefinition } from '../../../../types';

const theme = createTheme({ palette: { mode: 'dark' } });

function renderForm(ui: React.ReactElement) {
  return render(<ThemeProvider theme={theme}>{ui}</ThemeProvider>);
}

function makeNodeData(overrides: Partial<GraphNodeData> = {}): GraphNodeData {
  return {
    nodeId: 'node-1',
    title: 'Acme Corp',
    content: {},
    templateId: 'templates/company',
    tags: [],
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-02-02T00:00:00Z',
    ...overrides,
  };
}

function makeStringProp(overrides: Partial<PropertyDefinition> = {}): PropertyDefinition {
  return {
    name: 'industry',
    display_name: 'Industry',
    property_type: 'string',
    required: false,
    description: 'Industry sector',
    ...overrides,
  };
}

function makeTemplate(properties: PropertyDefinition[]): Template {
  return {
    id: 'templates/company',
    name: 'Company',
    template_type: 'node',
    properties,
    is_system: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };
}

interface FormPropsOverride {
  selectedNodeId?: string | null;
  selectedNodeData?: GraphNodeData;
  nodeTitle?: string;
  nodeProperties?: Record<string, unknown>;
  nodeTags?: string[];
  newTag?: string;
  hasChanges?: boolean;
  template?: Template | null;
  loadingTemplate?: boolean;
  getNodeSourceGroup?: (nodeId: string) => SourceGroupState | undefined;
  graph?: Graph<NodeAttributes, EdgeAttributes>;
}

interface Callbacks {
  onTitleChange: Mock<(title: string) => void>;
  onPropertyChange: Mock<(propName: string, value: unknown) => void>;
  onNewTagChange: Mock<(tag: string) => void>;
  onAddTag: Mock<() => void>;
  onDeleteTag: Mock<(tag: string) => void>;
  onMarkChanged: Mock<() => void>;
  onSave: Mock<() => void>;
  onDelete: Mock<() => void>;
  onSelectNode: Mock<(nodeId: string) => void>;
}

function makeCallbacks(): Callbacks {
  return {
    onTitleChange: vi.fn<(title: string) => void>(),
    onPropertyChange: vi.fn<(propName: string, value: unknown) => void>(),
    onNewTagChange: vi.fn<(tag: string) => void>(),
    onAddTag: vi.fn<() => void>(),
    onDeleteTag: vi.fn<(tag: string) => void>(),
    onMarkChanged: vi.fn<() => void>(),
    onSave: vi.fn<() => void>(),
    onDelete: vi.fn<() => void>(),
    onSelectNode: vi.fn<(nodeId: string) => void>(),
  };
}

function buildProps(cb: Callbacks, overrides: FormPropsOverride = {}) {
  return {
    selectedNodeId: 'node-1' as string | null,
    selectedNodeData: makeNodeData(),
    nodeTitle: 'Acme Corp',
    onTitleChange: cb.onTitleChange,
    nodeProperties: {} as Record<string, unknown>,
    onPropertyChange: cb.onPropertyChange,
    nodeTags: [] as string[],
    newTag: '',
    onNewTagChange: cb.onNewTagChange,
    onAddTag: cb.onAddTag,
    onDeleteTag: cb.onDeleteTag,
    hasChanges: false,
    onMarkChanged: cb.onMarkChanged,
    template: null as Template | null,
    loadingTemplate: false,
    onSave: cb.onSave,
    onDelete: cb.onDelete,
    onSelectNode: cb.onSelectNode,
    ...overrides,
  };
}

describe('NodePropertiesForm', () => {
  let cb: Callbacks;

  beforeEach(() => {
    cb = makeCallbacks();
  });

  it('renders the title field with the current title value', () => {
    renderForm(<NodePropertiesForm {...buildProps(cb, { nodeTitle: 'Acme Corp' })} />);
    const title = screen.getByRole('textbox', { name: 'Title' });
    expect(title).toHaveValue('Acme Corp');
  });

  it('calls onTitleChange and onMarkChanged when the title is edited', () => {
    renderForm(<NodePropertiesForm {...buildProps(cb)} />);
    const title = screen.getByRole('textbox', { name: 'Title' });
    fireEvent.change(title, { target: { value: 'New Title' } });
    expect(cb.onTitleChange).toHaveBeenCalledWith('New Title');
    expect(cb.onMarkChanged).toHaveBeenCalledTimes(1);
  });

  it('copies the node id to the clipboard from the metadata tooltip', async () => {
    const writeText = vi.fn<(text: string) => Promise<void>>(() => Promise.resolve());
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
      writable: true,
    });

    renderForm(
      <NodePropertiesForm {...buildProps(cb, { selectedNodeId: 'node-xyz' })} />,
    );
    // Tooltip content is rendered on hover; open the info button.
    fireEvent.mouseOver(screen.getByRole('button', { name: 'Show node info' }));
    const copyBtn = await screen.findByRole('button', { name: 'Copy' });
    fireEvent.click(copyBtn);
    expect(writeText).toHaveBeenCalledWith('node-xyz');
  });

  it('shows the loading indicator while the template is loading', () => {
    renderForm(<NodePropertiesForm {...buildProps(cb, { loadingTemplate: true })} />);
    expect(screen.getByText('Loading...')).toBeInTheDocument();
    // Edit Properties heading should not render while loading.
    expect(screen.queryByText('Edit Properties')).not.toBeInTheDocument();
  });

  it('renders template-driven property fields (string + boolean + enum)', () => {
    const template = makeTemplate([
      makeStringProp({ name: 'industry', display_name: 'Industry' }),
      { name: 'active', display_name: 'Active', property_type: 'boolean' },
      {
        name: 'tier',
        display_name: 'Tier',
        property_type: 'enum',
        enum_values: ['gold', 'silver'],
      },
    ]);
    renderForm(
      <NodePropertiesForm
        {...buildProps(cb, {
          template,
          nodeProperties: { industry: 'Tech', active: true, tier: 'gold' },
        })}
      />,
    );
    expect(screen.getByText('Edit Properties')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Industry' })).toHaveValue('Tech');
    // MUI Switch exposes role="switch" in jsdom.
    expect(screen.getByRole('switch')).toBeChecked();
    expect(screen.getByRole('combobox')).toBeInTheDocument();
  });

  it('forwards template field edits through onPropertyChange', () => {
    const template = makeTemplate([makeStringProp({ name: 'industry', display_name: 'Industry' })]);
    renderForm(
      <NodePropertiesForm
        {...buildProps(cb, { template, nodeProperties: { industry: 'Tech' } })}
      />,
    );
    fireEvent.change(screen.getByRole('textbox', { name: 'Industry' }), {
      target: { value: 'Finance' },
    });
    expect(cb.onPropertyChange).toHaveBeenCalledWith('industry', 'Finance');
  });

  it('renders content facts, formatting arrays and objects, hiding system/template keys', () => {
    // Template prop name "industry" formats to "Industry" as a fact key, but its
    // display_name is "Sector" so the only place "Industry" could appear is the
    // facts list — proving the template key is filtered out.
    const template = makeTemplate([makeStringProp({ name: 'industry', display_name: 'Sector' })]);
    renderForm(
      <NodePropertiesForm
        {...buildProps(cb, {
          template,
          nodeProperties: {
            industry: 'Tech', // template prop -> hidden from facts
            embedding: [0.1, 0.2], // system hidden key
            founded: '1999', // plain fact
            regions: ['EU', 'US'], // array fact
            meta: { ceo: 'Jane' }, // object fact
            blankFact: '', // empty -> filtered out
          },
        })}
      />,
    );
    expect(screen.getByText('Details')).toBeInTheDocument();
    // formatKey: founded -> "Founded"
    expect(screen.getByText('Founded')).toBeInTheDocument();
    expect(screen.getByText('1999')).toBeInTheDocument();
    // array joined with ", "
    expect(screen.getByText('EU, US')).toBeInTheDocument();
    // object JSON-stringified
    expect(screen.getByText(/"ceo": "Jane"/)).toBeInTheDocument();
    // template prop "industry" (formatKey -> "Industry") is filtered from facts;
    // the embedding system key is also hidden.
    expect(screen.queryByText('Industry')).not.toBeInTheDocument();
    expect(screen.queryByText('Embedding')).not.toBeInTheDocument();
  });

  it('inline-edits a fact and commits a JSON value via onPropertyChange', () => {
    renderForm(
      <NodePropertiesForm
        {...buildProps(cb, { nodeProperties: { notes: 'old value' } })}
      />,
    );
    // Enter edit mode for the single fact.
    fireEvent.click(screen.getByRole('button', { name: 'Edit property' }));
    const editField = screen.getByDisplayValue('old value');
    fireEvent.change(editField, { target: { value: '{"a":1}' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save property' }));
    expect(cb.onPropertyChange).toHaveBeenCalledWith('notes', { a: 1 });
    expect(cb.onMarkChanged).toHaveBeenCalled();
  });

  it('commits a plain-string fact edit via Enter key', () => {
    renderForm(
      <NodePropertiesForm
        {...buildProps(cb, { nodeProperties: { notes: 'old value' } })}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Edit property' }));
    const editField = screen.getByDisplayValue('old value');
    fireEvent.change(editField, { target: { value: 'fresh text' } });
    fireEvent.keyDown(editField, { key: 'Enter' });
    expect(cb.onPropertyChange).toHaveBeenCalledWith('notes', 'fresh text');
  });

  it('shows a "Show all" toggle when there are more than four facts', () => {
    const nodeProperties: Record<string, unknown> = {};
    for (let i = 0; i < 6; i++) nodeProperties[`fact_${i}`] = `value ${i}`;
    renderForm(<NodePropertiesForm {...buildProps(cb, { nodeProperties })} />);

    // Only first 4 visible initially.
    expect(screen.getByText('value 0')).toBeInTheDocument();
    expect(screen.queryByText('value 5')).not.toBeInTheDocument();

    const toggle = screen.getByRole('button', { name: /Show all 6 properties/ });
    fireEvent.click(toggle);
    expect(screen.getByText('value 5')).toBeInTheDocument();
    // Collapses again.
    fireEvent.click(screen.getByRole('button', { name: 'Show less' }));
    expect(screen.queryByText('value 5')).not.toBeInTheDocument();
  });

  it('renders tags and fires onDeleteTag when a chip is removed', () => {
    renderForm(
      <NodePropertiesForm {...buildProps(cb, { nodeTags: ['alpha', 'beta'] })} />,
    );
    expect(screen.getByText('alpha')).toBeInTheDocument();
    expect(screen.getByText('beta')).toBeInTheDocument();
    // MUI Chip delete button carries a CancelIcon; locate via the chip root.
    const alphaChip = screen.getByText('alpha').closest('.MuiChip-root');
    expect(alphaChip).not.toBeNull();
    const deleteIcon = alphaChip?.querySelector('.MuiChip-deleteIcon');
    expect(deleteIcon).not.toBeNull();
    fireEvent.click(deleteIcon as Element);
    expect(cb.onDeleteTag).toHaveBeenCalledWith('alpha');
  });

  it('adds a tag via the Add button and via Enter, and reports input changes', () => {
    renderForm(<NodePropertiesForm {...buildProps(cb, { newTag: 'draft' })} />);
    const tagInput = screen.getByRole('textbox', { name: 'Add tag' });
    fireEvent.change(tagInput, { target: { value: 'newtag' } });
    expect(cb.onNewTagChange).toHaveBeenCalledWith('newtag');

    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    expect(cb.onAddTag).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(tagInput, { key: 'Enter' });
    expect(cb.onAddTag).toHaveBeenCalledTimes(2);
  });

  it('renders connected nodes from the graph and navigates via onSelectNode', () => {
    const graph = new Graph<NodeAttributes, EdgeAttributes>();
    const baseAttrs = {
      content: {},
      templateId: 't',
      tags: [],
      createdAt: '',
      updatedAt: '',
      x: 0,
      y: 0,
      size: 1,
      color: '#fff',
    };
    graph.addNode('node-1', {
      ...baseAttrs,
      nodeId: 'node-1',
      title: 'Acme Corp',
      label: 'Acme Corp',
    });
    graph.addNode('node-2', {
      ...baseAttrs,
      nodeId: 'node-2',
      title: 'Globex',
      label: 'Globex',
    });
    graph.addEdgeWithKey('edge-1', 'node-1', 'node-2', {
      edgeId: 'edge-1',
      label: 'partners with',
      templateId: 't',
      sourceId: 'node-1',
      targetId: 'node-2',
      properties: {},
      createdAt: '',
      updatedAt: '',
    });

    renderForm(<NodePropertiesForm {...buildProps(cb, { graph })} />);
    expect(screen.getByText('Connections')).toBeInTheDocument();
    expect(screen.getByText('partners with')).toBeInTheDocument();
    expect(screen.getByText('Globex')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Go/ }));
    expect(cb.onSelectNode).toHaveBeenCalledWith('node-2');
  });

  it('renders the provenance section and selects the source group node', () => {
    const sourceGroup: SourceGroupState = {
      group: {
        source_id: 'src-9',
        title: 'Annual Report.pdf',
        source_type: 'pdf',
        filename: 'Annual Report.pdf',
        entity_count: 3,
        entity_node_ids: ['node-1'],
      },
      memberNodeIds: ['node-1'],
      externalNodeIds: new Set<string>(),
      expanded: false,
    };
    const getNodeSourceGroup = vi.fn<(nodeId: string) => SourceGroupState | undefined>(
      () => sourceGroup,
    );

    renderForm(
      <NodePropertiesForm {...buildProps(cb, { getNodeSourceGroup })} />,
    );
    expect(getNodeSourceGroup).toHaveBeenCalledWith('node-1');
    expect(screen.getByText('Extracted from')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Annual Report.pdf' }));
    expect(cb.onSelectNode).toHaveBeenCalledWith('sg:src-9');
  });

  it('disables Save when there are no changes and hides the unsaved alert', () => {
    renderForm(<NodePropertiesForm {...buildProps(cb, { hasChanges: false })} />);
    expect(screen.getByRole('button', { name: /Save/ })).toBeDisabled();
    expect(screen.queryByText('You have unsaved changes')).not.toBeInTheDocument();
  });

  it('enables Save and shows the alert when there are changes, firing onSave', () => {
    renderForm(<NodePropertiesForm {...buildProps(cb, { hasChanges: true })} />);
    const saveBtn = screen.getByRole('button', { name: /Save/ });
    expect(saveBtn).toBeEnabled();
    expect(screen.getByText('You have unsaved changes')).toBeInTheDocument();
    fireEvent.click(saveBtn);
    expect(cb.onSave).toHaveBeenCalledTimes(1);
  });

  it('fires onDelete when the Delete button is clicked', () => {
    renderForm(<NodePropertiesForm {...buildProps(cb)} />);
    fireEvent.click(screen.getByRole('button', { name: /Delete/ }));
    expect(cb.onDelete).toHaveBeenCalledTimes(1);
  });

  it('opens the enum Select and forwards the chosen value', async () => {
    const template = makeTemplate([
      {
        name: 'tier',
        display_name: 'Tier',
        property_type: 'enum',
        enum_values: ['gold', 'silver'],
      },
    ]);
    renderForm(
      <NodePropertiesForm
        {...buildProps(cb, { template, nodeProperties: { tier: 'gold' } })}
      />,
    );
    fireEvent.mouseDown(screen.getByRole('combobox'));
    const listbox = await screen.findByRole('listbox');
    fireEvent.click(within(listbox).getByText('silver'));
    await waitFor(() => expect(cb.onPropertyChange).toHaveBeenCalledWith('tier', 'silver'));
  });

  it('drives a full edit cycle via a stateful wrapper (title + save enable)', () => {
    function Harness() {
      const [titleVal, setTitleVal] = useState('Acme Corp');
      const [changed, setChanged] = useState(false);
      return (
        <NodePropertiesForm
          {...buildProps(cb, { hasChanges: changed })}
          nodeTitle={titleVal}
          onTitleChange={setTitleVal}
          onMarkChanged={() => setChanged(true)}
        />
      );
    }
    renderForm(<Harness />);
    expect(screen.getByRole('button', { name: /Save/ })).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox', { name: 'Title' }), {
      target: { value: 'Acme Corporation' },
    });
    expect(screen.getByRole('textbox', { name: 'Title' })).toHaveValue('Acme Corporation');
    expect(screen.getByRole('button', { name: /Save/ })).toBeEnabled();
  });
});
