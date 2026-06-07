// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Animated loading dots indicator.
 *
 * Cycles through ".", "..", "..." on a 500ms interval.
 * Used as the "Thinking..." placeholder while waiting for AI responses.
 */

import { useState, useEffect } from 'react';

export default function LoadingDots() {
  const [dots, setDots] = useState('.');

  useEffect(() => {
    const interval = setInterval(() => {
      setDots(prev => {
        if (prev === '.') return '..';
        if (prev === '..') return '...';
        return '.';
      });
    }, 500);

    return () => clearInterval(interval);
  }, []);

  return <span>{dots}</span>;
}
