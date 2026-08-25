// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for the error-narrowing helpers, focused on isTemplateInUseError:
 * the force-delete affordance on the Templates pages keys off it, and it
 * must match the backend's 409 TEMPLATE_IN_USE contract by status + code —
 * never by the human-readable message (which the backend rewrites; the
 * historical message-substring match never fired in production).
 */

import { describe, it, expect } from 'vitest';
import { isTemplateInUseError, getApiErrorMessage } from '../errors';

/** The exact shape the API client throws for a blocked template delete. */
function templateInUse409(): object {
  return {
    isApiError: true,
    status: 409,
    message: 'Template cannot be deleted because it is in use',
    response: {
      status: 409,
      data: {
        error: 'TEMPLATE_IN_USE',
        message: 'Template cannot be deleted because it is in use',
      },
    },
  };
}

describe('isTemplateInUseError', () => {
  it('matches the unified 409 TEMPLATE_IN_USE envelope', () => {
    expect(isTemplateInUseError(templateInUse409())).toBe(true);
  });

  it('matches a bare 409 without a machine code', () => {
    expect(
      isTemplateInUseError({ isApiError: true, status: 409, message: 'Conflict' }),
    ).toBe(true);
  });

  it('matches the legacy nested detail shape', () => {
    expect(
      isTemplateInUseError({
        status: 409,
        response: {
          status: 409,
          data: { detail: { code: 'TEMPLATE_IN_USE', message: 'in use' } },
        },
      }),
    ).toBe(true);
  });

  it('rejects a 409 carrying a different machine code', () => {
    expect(
      isTemplateInUseError({
        status: 409,
        response: { status: 409, data: { error: 'SOMETHING_ELSE', message: 'nope' } },
      }),
    ).toBe(false);
  });

  it('rejects non-409 statuses regardless of message text', () => {
    expect(
      isTemplateInUseError({
        status: 400,
        message: 'currently used by 3 nodes (force=True to override)',
      }),
    ).toBe(false);
  });

  it('rejects non-object errors', () => {
    expect(isTemplateInUseError('currently used by nodes')).toBe(false);
    expect(isTemplateInUseError(undefined)).toBe(false);
    expect(isTemplateInUseError(null)).toBe(false);
  });
});

describe('getApiErrorMessage (envelope precedence)', () => {
  it('prefers the unified envelope message', () => {
    expect(getApiErrorMessage(templateInUse409())).toBe(
      'Template cannot be deleted because it is in use',
    );
  });
});
