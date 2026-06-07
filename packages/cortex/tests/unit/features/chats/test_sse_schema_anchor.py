# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Schema-anchor endpoint returns a clean 501, not a 500.

``GET /api/v1/chats/_schema/sse_event`` is a schema-only operation kept
visible so Phase 7 TS codegen can register ``ChatSSEEnvelope`` in
``#/components/schemas``. Any actual call is a fuzzer probe and must
return 501 — the previous ``raise NotImplementedError`` produced an
uncaught 500 that lit up monitoring on every probe.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException, status

from chaoscypher_cortex.features.chats.api import sse_event_schema_anchor


@pytest.mark.asyncio
async def test_schema_anchor_raises_501_not_500() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await sse_event_schema_anchor()

    assert exc_info.value.status_code == status.HTTP_501_NOT_IMPLEMENTED
