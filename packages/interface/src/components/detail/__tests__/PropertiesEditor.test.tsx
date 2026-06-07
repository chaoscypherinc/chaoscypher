// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import PropertiesEditor from '../PropertiesEditor';

const PROPERTIES = {
  occupation: 'mathematician',
  source_document_id: 'doc-1',
  source_document_name: 'war_and_peace.txt',
};

describe('PropertiesEditor excludeKeys', () => {
  it('hides excluded keys from the read-only list but renders the rest', () => {
    render(
      <PropertiesEditor
        properties={PROPERTIES}
        editing={false}
        onChange={() => {}}
        excludeKeys={new Set(['source_document_id', 'source_document_name'])}
      />,
    );
    expect(screen.getByText('occupation')).toBeTruthy();
    expect(screen.queryByText('source_document_id')).toBeNull();
    expect(screen.queryByText('source_document_name')).toBeNull();
  });

  it('hides excluded keys while editing without dropping them on change', () => {
    const onChange = vi.fn();
    render(
      <PropertiesEditor
        properties={PROPERTIES}
        editing
        onChange={onChange}
        excludeKeys={new Set(['source_document_id', 'source_document_name'])}
      />,
    );

    // Excluded keys have no editable field.
    expect(screen.queryByLabelText('source_document_id')).toBeNull();
    const occupation = screen.getByLabelText('occupation') as HTMLInputElement;
    fireEvent.change(occupation, { target: { value: 'engineer' } });

    // The emitted object preserves the excluded provenance keys untouched.
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        occupation: 'engineer',
        source_document_id: 'doc-1',
        source_document_name: 'war_and_peace.txt',
      }),
    );
  });
});
