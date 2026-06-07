// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * The section wraps three existing chart components with a
 * click-to-expand affordance. Tests focus on the new behaviour
 * (which chart is expanded at a time) rather than re-asserting the
 * chart components' own rendering — that's covered by their own
 * tests.
 *
 * Because recharts in jsdom doesn't paint full SVG content, we mock
 * the inner chart components to keep the test deterministic and
 * focused on the wrapper logic.
 */

import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

// Mock the three chart components — they import recharts which is
// noisy in jsdom and unrelated to the wrapper behaviour under test.
vi.mock('../../charts', () => ({
  ContextUtilizationChart: () => <div data-testid="ctx-chart" />,
  ProcessingTimeChart: () => <div data-testid="time-chart" />,
  EntityDensityChart: () => <div data-testid="density-chart" />,
}));

import { PerformanceChartsSection } from '../PerformanceChartsSection';
import type { ExtractionChartTask, ExtractionTask } from '../../../../../types';

function fakeTask(i: number): ExtractionTask {
  return {
    id: `t${i}`,
    job_id: 'j1',
    chunk_index: i,
    status: 'completed',
    created_at: '2026-05-11T10:00:00Z',
    retry_count: 0,
    entity_count: 5,
    relationship_count: 3,
    invalid_relationship_count: 0,
  } as ExtractionTask;
}

function fakeChartTask(i: number): ExtractionChartTask {
  return {
    id: `t${i}`,
    chunk_index: i,
    status: 'completed',
    retry_count: 0,
    entity_count: 5,
    relationship_count: 3,
    invalid_relationship_count: 0,
  };
}

describe('<PerformanceChartsSection />', () => {
  it('renders all three chart cards but only two expand affordances', () => {
    // Context utilization deliberately omits the expand button because
    // its default render is already full-width with detailed bars.
    render(
      <PerformanceChartsSection
        tasks={[fakeTask(1), fakeTask(2)]}
        chartTasks={[fakeChartTask(1), fakeChartTask(2)]}
        stats={null}
      />,
    );
    expect(screen.getByTestId('ctx-chart')).toBeInTheDocument();
    expect(screen.queryByLabelText(/Expand ⚡ Context utilization/i)).toBeNull();
    expect(screen.getByLabelText(/Expand ⏱ Processing time/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Expand ⊜ Entity density/i)).toBeInTheDocument();
  });

  it('expands the chosen chart inline when its expand button is clicked', () => {
    render(
      <PerformanceChartsSection
        tasks={[fakeTask(1)]}
        chartTasks={[fakeChartTask(1)]}
        stats={null}
      />,
    );

    fireEvent.click(screen.getByLabelText(/Expand ⏱ Processing time/i));
    expect(screen.getByTestId('performance-expanded-time')).toBeInTheDocument();
  });

  it('enforces single-chart expansion — clicking a second chart switches', () => {
    render(
      <PerformanceChartsSection
        tasks={[fakeTask(1)]}
        chartTasks={[fakeChartTask(1)]}
        stats={null}
      />,
    );

    fireEvent.click(screen.getByLabelText(/Expand ⏱ Processing time/i));
    expect(screen.getByTestId('performance-expanded-time')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText(/Expand ⊜ Entity density/i));
    expect(screen.queryByTestId('performance-expanded-time')).toBeNull();
    expect(screen.getByTestId('performance-expanded-density')).toBeInTheDocument();
  });

  it('closes the expanded chart when the close button is clicked', () => {
    render(
      <PerformanceChartsSection
        tasks={[fakeTask(1)]}
        chartTasks={[fakeChartTask(1)]}
        stats={null}
      />,
    );
    fireEvent.click(screen.getByLabelText(/Expand ⏱ Processing time/i));
    expect(screen.getByTestId('performance-expanded-time')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('Close expanded chart'));
    expect(screen.queryByTestId('performance-expanded-time')).toBeNull();
  });

  it('auto-hides (renders nothing) when chartTasks + tasks are both empty', () => {
    const { container } = render(
      <PerformanceChartsSection tasks={[]} chartTasks={[]} stats={null} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
