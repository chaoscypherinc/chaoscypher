// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { LLMStageInline, LLMStageTooltip } from '../LLMStageProgress';


describe('LLMStageInline', () => {
  it('renders X/Y items count', () => {
    render(<LLMStageInline processed={47} total={184} itemNoun="pages" />);
    expect(screen.getByText(/47\/184 pages/)).toBeInTheDocument();
  });

  it('omits remaining when avgMs is null', () => {
    render(<LLMStageInline processed={47} total={184} itemNoun="pages" avgMs={null} />);
    expect(screen.queryByText(/~/)).not.toBeInTheDocument();
  });

  it('shows remaining when avgMs is present and processed < total', () => {
    render(<LLMStageInline processed={47} total={184} itemNoun="pages" avgMs={10000} />);
    expect(screen.getByText(/~/)).toBeInTheDocument();
  });

  it('returns null when total is 0', () => {
    const { container } = render(
      <LLMStageInline processed={0} total={0} itemNoun="pages" />
    );
    expect(container.firstChild).toBeNull();
  });
});

describe('LLMStageTooltip', () => {
  it('renders label and counts', () => {
    render(
      <LLMStageTooltip label="Vision processing" processed={47} total={184} itemNoun="pages" />
    );
    expect(screen.getByText('Vision processing')).toBeInTheDocument();
    expect(screen.getByText(/47 \/ 184 pages/)).toBeInTheDocument();
  });

  it('shows avg line when avgMs is present', () => {
    render(
      <LLMStageTooltip
        label="Vision processing" processed={47} total={184}
        itemNoun="pages" avgMs={8200}
      />
    );
    expect(screen.getByText(/Avg ~8\.2s/)).toBeInTheDocument();
  });

  it('renders extra children in extras slot', () => {
    render(
      <LLMStageTooltip label="x" processed={1} total={2} itemNoun="y">
        <span data-testid="extra">extra content</span>
      </LLMStageTooltip>
    );
    expect(screen.getByTestId('extra')).toBeInTheDocument();
  });
});
