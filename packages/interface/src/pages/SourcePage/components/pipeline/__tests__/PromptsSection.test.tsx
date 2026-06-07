// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect } from 'vitest';
import type { ExtractionTaskStats } from '../../../../../types';
import { PromptsSection } from '../PromptsSection';

const stats = {
  system_prompt: 'You are an extraction assistant.',
  // Entity (pass 1) prompt template with a chunk-text placeholder.
  user_instructions: 'Find entities in [[ CHUNK TEXT ]] now.',
  // Relationship (pass 2) prompt template with two placeholders.
  relationship_instructions: 'Using [[ PASS-1 ENTITIES ]] from [[ CHUNK TEXT ]] find links.',
} as unknown as ExtractionTaskStats;

/** Expand the outer "AI prompts" accordion so the inner prompts mount. */
async function openAccordion() {
  await userEvent.click(screen.getByRole('button', { name: /AI prompts/i }));
}

describe('PromptsSection', () => {
  it('renders nothing when the source has no prompt data', () => {
    const { container } = render(<PromptsSection stats={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('is collapsed on load — the prompt body is not rendered until opened', () => {
    render(<PromptsSection stats={stats} />);
    const summary = screen.getByRole('button', { name: /AI prompts/i });
    expect(summary).toHaveAttribute('aria-expanded', 'false');
    // Inner prompt content is not mounted while collapsed.
    expect(screen.queryByText('Entity extraction prompt (Pass 1)')).toBeNull();
  });

  it('reveals both prompt headers once the accordion is expanded', async () => {
    render(<PromptsSection stats={stats} />);
    await openAccordion();
    expect(screen.getByText('Entity extraction prompt (Pass 1)')).toBeInTheDocument();
    expect(screen.getByText('Relationship extraction prompt (Pass 2)')).toBeInTheDocument();
  });

  it('expands the entity prompt and highlights the chunk-text placeholder', async () => {
    render(<PromptsSection stats={stats} />);
    await openAccordion();
    // Inner prompt body still collapsed until its own header is clicked.
    expect(screen.queryByText('[[ CHUNK TEXT ]]')).toBeNull();

    await userEvent.click(screen.getByText('Entity extraction prompt (Pass 1)'));

    const placeholder = screen.getByText('[[ CHUNK TEXT ]]');
    expect(placeholder).toBeInTheDocument();
    expect(placeholder).toHaveAttribute('data-testid', 'prompt-placeholder');
  });

  it('expands the relationship prompt and highlights both placeholders', async () => {
    render(<PromptsSection stats={stats} />);
    await openAccordion();

    await userEvent.click(screen.getByText('Relationship extraction prompt (Pass 2)'));

    expect(screen.getByText('[[ PASS-1 ENTITIES ]]')).toBeInTheDocument();
    expect(screen.getByText('[[ CHUNK TEXT ]]')).toBeInTheDocument();
    expect(screen.getAllByTestId('prompt-placeholder')).toHaveLength(2);
  });

  it('omits the relationship prompt section on legacy sources without it', async () => {
    const legacy = {
      system_prompt: 'You are an extraction assistant.',
      user_instructions: 'Find entities in this chunk.',
    } as unknown as ExtractionTaskStats;
    render(<PromptsSection stats={legacy} />);
    await openAccordion();
    expect(screen.getByText('Entity extraction prompt (Pass 1)')).toBeInTheDocument();
    expect(screen.queryByText('Relationship extraction prompt (Pass 2)')).toBeNull();
  });
});
