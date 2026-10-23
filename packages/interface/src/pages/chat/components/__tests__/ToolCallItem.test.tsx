// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material';

import ToolCallItem from '../ToolCallItem';
import sample from './graphBlocks.sample.json';

function renderItem(toolName: string, content: string) {
  render(
    <ThemeProvider theme={createTheme()}>
      <ToolCallItem
        toolName={toolName}
        compactArgs="query"
        call={{ id: 'tc-1', function: { name: toolName, arguments: '{}' } }}
        matchingResult={{ tool_call_id: 'tc-1', content }}
        isLast
      />
    </ThemeProvider>,
  );
}

describe('ToolCallItem', () => {
  it('shows a graphrag result’s shape and graph lines above its raw output', () => {
    renderItem('graphrag_search', JSON.stringify(sample.result));
    // the caption, then the lines in order, then the raw output
    const shown = [sample.caption, ...sample.lines, 'Output:'].map((text) => screen.getByText(text));
    shown.slice(1).forEach((after, index) => {
      expect(shown[index].compareDocumentPosition(after) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });
  });

  it('shows no graph block for another tool or a graphrag result without a shape', () => {
    renderItem('search_nodes', JSON.stringify(sample.result));
    expect(screen.queryByText(sample.caption)).toBeNull();
    const unshaped: Record<string, unknown> = { ...sample.result };
    delete unshaped.shape;
    renderItem('graphrag_search', JSON.stringify(unshaped));
    expect(screen.queryByText(sample.caption)).toBeNull();
    expect(screen.queryByText(sample.lines[0])).toBeNull();
  });
});
