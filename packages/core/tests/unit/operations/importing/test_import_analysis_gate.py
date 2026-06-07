# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Worker-wiring tests for the confirmation gate in _import_analysis_handler.

Covers:
- park path: gate parks an auto/unconfirmed source WITHOUT claiming the slot
- proceed path: forced/confirmed source claims the slot as before
- detection is hoisted to BEFORE the slot claim

Patch targets follow the established convention in this package
(test_analysis_handler_releases_slot_on_exception.py): symbols imported
function-locally inside ``_import_analysis_handler`` are patched at their
SOURCE module (``pause_guard.check_paused``, ``orchestration.``
``detect_extraction_domain``, ``domains.get_domain_registry``,
``confirmation_gate.park_for_confirmation``) so the in-function
``from ... import`` rebind picks the patch up; ``get_settings`` is patched
at ``chaoscypher_core.app_config.get_settings``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.models import SourceStatus
from chaoscypher_core.operations.importing.import_service import (
    ImportOperationsService,
)


def _make_service(source_repository: object) -> ImportOperationsService:
    from chaoscypher_core.settings import EngineSettings

    return ImportOperationsService(
        graph_repository=MagicMock(),
        config_manager=MagicMock(),
        source_manager=MagicMock(),
        trigger_service=MagicMock(),
        llm_service=AsyncMock(),
        source_repository=source_repository,
        chunking_service=MagicMock(),
        indexing_service=MagicMock(),
        # Worker context supplies cached EngineSettings; the handler reads
        # engine-relevant groups (domain registry, chunking/analysis) from it.
        engine_settings=EngineSettings(current_database="default"),
    )


def _make_settings() -> MagicMock:
    settings = MagicMock()
    settings.current_database = "default"
    return settings


def _auto_indexed_source() -> dict[str, Any]:
    return {
        "id": "src-1",
        "status": SourceStatus.INDEXED,
        "forced_domain": None,
        "confirmation_required": True,
        "extraction_confirmed_at": None,
        "filename": "doc.pdf",
        "filepath": "/tmp/doc.pdf",
    }


@pytest.mark.asyncio
async def test_park_does_not_claim_slot() -> None:
    """Auto + confirmation_required + INDEXED parks and never claims the slot."""
    # unsafe=True so ``adapter.assert_extractable`` is a callable attribute
    # rather than tripping MagicMock's assertion-name guardrail.
    adapter = MagicMock(unsafe=True)
    adapter.get_source.return_value = _auto_indexed_source()
    adapter.assert_extractable.return_value = None
    # Detection sample text + chunks needed by the hoisted detect.
    adapter.get_chunks_for_extraction.return_value = [{"content": "hello world"}]

    service = _make_service(adapter)

    detect_result = {
        "domain": MagicMock(),
        "detected_domain": "generic",
        "confidence": 0.1,
        "ranking": [],
        "low_confidence": True,
        "entity_guidance": "",
        "relationship_guidance": "",
    }

    with (
        patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=_make_settings(),
        ),
        patch(
            "chaoscypher_core.operations.pause_guard.check_paused",
            return_value=MagicMock(paused=False),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=MagicMock(),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.orchestration.detect_extraction_domain",
            return_value=detect_result,
        ),
        patch(
            "chaoscypher_core.operations.importing.confirmation_gate.park_for_confirmation"
        ) as park,
    ):
        result = await service._import_analysis_handler(
            data={"file_id": "src-1", "file_info": {"filename": "doc.pdf"}}
        )

    park.assert_called_once()
    adapter.try_claim_extraction.assert_not_called()
    assert result.get("status") == "parked"


@pytest.mark.asyncio
async def test_forced_domain_proceeds_to_claim() -> None:
    """Forced-domain source bypasses the gate and claims the slot as before."""
    src = _auto_indexed_source()
    src["forced_domain"] = "technical"
    adapter = MagicMock(unsafe=True)
    adapter.get_source.return_value = src
    adapter.assert_extractable.return_value = None
    adapter.get_chunks_for_extraction.return_value = [{"content": "hello"}]
    # Slot claim succeeds -> proceed path continues; short-circuit by making
    # the very next adapter call raise so we only assert the claim happened.
    adapter.try_claim_extraction.return_value = True
    adapter.clear_extraction_waiting.side_effect = RuntimeError("stop-after-claim")

    service = _make_service(adapter)

    detect_result = {
        "domain": MagicMock(),
        "detected_domain": "technical",
        "confidence": 1.0,
        "ranking": [{"domain": "technical", "score": 1.0}],
        "low_confidence": False,
        "entity_guidance": "",
        "relationship_guidance": "",
    }

    with (
        patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=_make_settings(),
        ),
        patch(
            "chaoscypher_core.operations.pause_guard.check_paused",
            return_value=MagicMock(paused=False),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=MagicMock(),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.orchestration.detect_extraction_domain",
            return_value=detect_result,
        ),
        patch(
            "chaoscypher_core.operations.importing.confirmation_gate.park_for_confirmation"
        ) as park,
        patch(
            "chaoscypher_core.operations.importing.import_service.trigger_next_waiting_extraction",
            new=AsyncMock(),
        ),
    ):
        with pytest.raises(RuntimeError, match="stop-after-claim"):
            await service._import_analysis_handler(
                data={"file_id": "src-1", "file_info": {"filename": "doc.pdf"}}
            )

    park.assert_not_called()
    adapter.try_claim_extraction.assert_called_once()


@pytest.mark.asyncio
async def test_proceed_detects_once_and_threads_result() -> None:
    """Proceed path detects exactly once and threads the result into create.

    Proves no double-detection: the hoisted gate computes ``domain_result`` and
    passes it to ``_create_fresh_extraction_job`` (which would otherwise detect
    again). ``detect_extraction_domain`` must be called exactly once.
    """
    src = _auto_indexed_source()
    src["forced_domain"] = "technical"
    adapter = MagicMock(unsafe=True)
    adapter.get_source.return_value = src
    adapter.assert_extractable.return_value = None
    adapter.get_chunks_for_extraction.return_value = [{"content": "hello"}]
    adapter.get_active_extraction_job.return_value = None
    adapter.try_claim_extraction.return_value = True
    # Stop right after the create call so we can assert detection-once + threading.
    adapter.clear_extraction_waiting.return_value = None
    adapter.update_step_progress.return_value = None

    service = _make_service(adapter)

    detect_result = {
        "domain": MagicMock(),
        "detected_domain": "technical",
        "confidence": 1.0,
        "ranking": [{"domain": "technical", "score": 1.0}],
        "low_confidence": False,
        "entity_guidance": "",
        "relationship_guidance": "",
    }

    # Raise from inside the create call so the body short-circuits after the
    # gate has threaded domain_result in — keeps the test focused.
    create = MagicMock(side_effect=RuntimeError("stop-after-create"))

    with (
        patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=_make_settings(),
        ),
        patch(
            "chaoscypher_core.operations.pause_guard.check_paused",
            return_value=MagicMock(paused=False),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=MagicMock(),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.orchestration.detect_extraction_domain",
            return_value=detect_result,
        ) as detect,
        patch(
            "chaoscypher_core.operations.importing.import_service._create_fresh_extraction_job",
            new=create,
        ),
        patch("chaoscypher_core.operations.importing.import_service.event_bus"),
        patch(
            "chaoscypher_core.operations.importing.import_service.trigger_next_waiting_extraction",
            new=AsyncMock(),
        ),
    ):
        with pytest.raises(RuntimeError, match="stop-after-create"):
            await service._import_analysis_handler(
                data={"file_id": "src-1", "file_info": {"filename": "doc.pdf"}}
            )

    # Detection ran exactly once (in the gate), not again inside create.
    detect.assert_called_once()
    # The gate's result was threaded into the create call.
    create.assert_called_once()
    assert create.call_args.kwargs["domain_result"] is detect_result


@pytest.mark.asyncio
async def test_recovery_redispatch_reparks_without_bypass() -> None:
    """Recovery re-dispatch cannot bypass the gate.

    A recovery re-dispatch carries no bypass in its payload —
    ``_classify_indexed`` builds ``{file_id, file_info, analysis_depth}`` only.
    The handler reads only persisted SourceRow state so an unconfirmed
    auto-analyze INDEXED source re-parks identically on re-dispatch.
    """
    # unsafe=True: adapter.assert_extractable starts with "assert_", which
    # MagicMock treats as an assertion helper unless unsafe=True is set.
    adapter = MagicMock(unsafe=True)
    adapter.get_source.return_value = _auto_indexed_source()
    adapter.assert_extractable.return_value = None
    adapter.get_chunks_for_extraction.return_value = [{"content": "hello"}]
    service = _make_service(adapter)

    detect_result = {
        "domain": MagicMock(),
        "detected_domain": "generic",
        "confidence": 0.1,
        "ranking": [],
        "low_confidence": True,
        "entity_guidance": "",
        "relationship_guidance": "",
    }

    with (
        patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=_make_settings(),
        ),
        patch(
            "chaoscypher_core.operations.pause_guard.check_paused",
            return_value=MagicMock(paused=False),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=MagicMock(),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.orchestration.detect_extraction_domain",
            return_value=detect_result,
        ),
        patch(
            "chaoscypher_core.operations.importing.confirmation_gate.park_for_confirmation"
        ) as park,
    ):
        # Recovery payload shape: no auto_confirm / bypass key.
        result = await service._import_analysis_handler(
            data={
                "file_id": "src-1",
                "file_info": {"filename": "doc.pdf"},
                "analysis_depth": "full",
            }
        )

    park.assert_called_once()
    adapter.try_claim_extraction.assert_not_called()
    assert result.get("status") == "parked"


@pytest.mark.asyncio
async def test_recovery_redispatch_uses_persisted_forced_domain() -> None:
    """Recovery re-dispatch of a CONFIRMED source must use its forced_domain.

    Regression: after confirm sets ``SourceRow.forced_domain=medical`` (and
    ``extraction_confirmed_at``), a worker crash before the create call leaves
    the source to be re-dispatched by recovery. ``_build_file_info_from_source``
    rebuilds ``file_info`` WITHOUT ``forced_domain``, so the handler used to read
    ``forced_domain=None`` and re-run auto-detection — extracting under a
    possibly-different domain and defeating the confirmation guarantee. The fix
    falls back to the persisted ``SourceRow.forced_domain`` (row is truth), so
    BOTH detection and ``_create_fresh_extraction_job`` receive ``medical``.
    """
    src = _auto_indexed_source()
    src["forced_domain"] = "medical"  # persisted by confirm
    src["extraction_confirmed_at"] = "2026-05-28T00:00:00+00:00"
    adapter = MagicMock(unsafe=True)
    adapter.get_source.return_value = src
    adapter.assert_extractable.return_value = None
    adapter.get_chunks_for_extraction.return_value = [{"content": "hello"}]
    adapter.get_active_extraction_job.return_value = None
    adapter.try_claim_extraction.return_value = True
    adapter.clear_extraction_waiting.return_value = None
    adapter.update_step_progress.return_value = None
    service = _make_service(adapter)

    detect_result = {
        "domain": MagicMock(),
        "detected_domain": "medical",
        "confidence": 1.0,
        "ranking": [{"domain": "medical", "score": 1.0}],
        "low_confidence": False,
        "entity_guidance": "",
        "relationship_guidance": "",
    }

    # Short-circuit right after the create call so the test stays focused.
    create = MagicMock(side_effect=RuntimeError("stop-after-create"))

    with (
        patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=_make_settings(),
        ),
        patch(
            "chaoscypher_core.operations.pause_guard.check_paused",
            return_value=MagicMock(paused=False),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=MagicMock(),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.orchestration.detect_extraction_domain",
            return_value=detect_result,
        ) as detect,
        patch(
            "chaoscypher_core.operations.importing.import_service._create_fresh_extraction_job",
            new=create,
        ),
        patch("chaoscypher_core.operations.importing.import_service.event_bus"),
        patch(
            "chaoscypher_core.operations.importing.confirmation_gate.park_for_confirmation"
        ) as park,
        patch(
            "chaoscypher_core.operations.importing.import_service.trigger_next_waiting_extraction",
            new=AsyncMock(),
        ),
    ):
        # Recovery payload shape: file_info WITHOUT forced_domain.
        with pytest.raises(RuntimeError, match="stop-after-create"):
            await service._import_analysis_handler(
                data={
                    "file_id": "src-1",
                    "file_info": {"filename": "doc.pdf"},
                    "analysis_depth": "full",
                }
            )

    # Confirmed source never re-parks.
    park.assert_not_called()
    # Detection ran under the confirmed domain (no re-auto-detection).
    detect.assert_called_once()
    assert detect.call_args.kwargs["forced_domain"] == "medical"
    # The create call received the confirmed domain from the row.
    create.assert_called_once()
    assert create.call_args.kwargs["forced_domain"] == "medical"


@pytest.mark.asyncio
async def test_confirmed_source_short_circuits_never_reparks() -> None:
    """A source with extraction_confirmed_at set proceeds without re-parking.

    ``extraction_confirmed_at`` set means the user already confirmed the
    domain; even though ``confirmation_required`` is still True the gate
    short-circuits to proceed.  The slot is claimed and ``park_for_confirmation``
    is never called.
    """
    src = _auto_indexed_source()
    src["extraction_confirmed_at"] = "2026-05-28T00:00:00+00:00"
    src["forced_domain"] = "technical"
    adapter = MagicMock(unsafe=True)
    adapter.get_source.return_value = src
    adapter.assert_extractable.return_value = None
    adapter.get_chunks_for_extraction.return_value = [{"content": "hello"}]
    adapter.try_claim_extraction.return_value = True
    adapter.clear_extraction_waiting.side_effect = RuntimeError("stop-after-claim")
    service = _make_service(adapter)

    detect_result = {
        "domain": MagicMock(),
        "detected_domain": "technical",
        "confidence": 1.0,
        "ranking": [{"domain": "technical", "score": 1.0}],
        "low_confidence": False,
        "entity_guidance": "",
        "relationship_guidance": "",
    }

    with (
        patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=_make_settings(),
        ),
        patch(
            "chaoscypher_core.operations.pause_guard.check_paused",
            return_value=MagicMock(paused=False),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=MagicMock(),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.orchestration.detect_extraction_domain",
            return_value=detect_result,
        ),
        patch(
            "chaoscypher_core.operations.importing.confirmation_gate.park_for_confirmation"
        ) as park,
        patch(
            "chaoscypher_core.operations.importing.import_service.trigger_next_waiting_extraction",
            new=AsyncMock(),
        ),
    ):
        with pytest.raises(RuntimeError, match="stop-after-claim"):
            await service._import_analysis_handler(
                data={"file_id": "src-1", "file_info": {"filename": "doc.pdf"}}
            )

    park.assert_not_called()
    adapter.try_claim_extraction.assert_called_once()
