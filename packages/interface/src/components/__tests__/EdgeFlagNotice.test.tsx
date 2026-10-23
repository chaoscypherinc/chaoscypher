// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { EdgeFlagNotice } from '../EdgeFlagNotice';

const REASON = 'neither "Prince Vasíli" nor "Anna Pávlovna" is named in S15';

describe('EdgeFlagNotice', () => {
  it('names the flag and shows the reason the pipeline gave', () => {
    render(<EdgeFlagNotice properties={{ unevidenced: true, evidence_reason: REASON }} />);
    expect(screen.getByText('Unevidenced')).toBeInTheDocument();
    expect(screen.getByText(REASON)).toBeInTheDocument();
    expect(screen.getByLabelText(/No cited sentence states this relationship/)).toBeInTheDocument();
  });

  it('shows every flag the edge carries, in table order', () => {
    render(<EdgeFlagNotice properties={{ capped: true, unevidenced: true, evidence_reason: REASON }} />);
    expect(screen.getByText('Capped')).toBeInTheDocument();
    expect(screen.getByText('Unevidenced')).toBeInTheDocument();
    expect(screen.getByText(REASON)).toBeInTheDocument();
  });

  it('renders nothing for an unflagged edge or a non-boolean flag value', () => {
    const { container, rerender } = render(
      <EdgeFlagNotice properties={{ justification: 'A fact.' }} />,
    );
    expect(container).toBeEmptyDOMElement();
    rerender(<EdgeFlagNotice properties={{ unevidenced: 'true', evidence_reason: REASON }} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<EdgeFlagNotice properties={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });
});
