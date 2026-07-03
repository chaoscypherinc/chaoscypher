# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for DocumentProcessor background queue."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from chaoscypher_core.mcp.processor import DocumentProcessor


@pytest.fixture
def processor(tmp_path):
    """Create a DocumentProcessor with mock pipeline."""
    # Create a temp file so file-exists check passes
    test_file = tmp_path / "test.pdf"
    test_file.write_text("fake content")
    proc = DocumentProcessor(
        pipeline_callback=AsyncMock(return_value={"nodes_created": 5, "edges_created": 10})
    )
    proc._test_file = str(test_file)  # for convenience
    return proc


class TestDocumentProcessorQueue:
    """Queue behavior: add, status, sequential processing."""

    @pytest.mark.asyncio
    async def test_add_first_document_starts_processing(self, processor):
        result = await processor.add_document(processor._test_file)
        assert result["status"] in ("processing", "queued")
        assert "file_id" in result
        processor.cancel()

    @pytest.mark.asyncio
    async def test_add_nonexistent_file_returns_error(self, processor):
        result = await processor.add_document("/nonexistent/path.pdf")
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_add_while_busy_queues(self, processor, tmp_path):
        # Make pipeline slow so first job stays active
        processor.pipeline_callback = AsyncMock(side_effect=lambda *a, **kw: asyncio.sleep(10))
        file_a = tmp_path / "a.pdf"
        file_a.write_text("content a")
        file_b = tmp_path / "b.pdf"
        file_b.write_text("content b")

        await processor.add_document(str(file_a))
        # Give worker a moment to pick up first job
        await asyncio.sleep(0.05)
        r2 = await processor.add_document(str(file_b))
        assert r2["status"] == "queued"
        assert r2["position"] >= 1
        processor.cancel()

    @pytest.mark.asyncio
    async def test_status_empty_when_idle(self, processor):
        status = processor.get_status()
        assert status["current"] is None
        assert status["queued"] == []
        assert status["completed"] == []

    @pytest.mark.asyncio
    async def test_completed_list_capped(self, processor):
        from chaoscypher_core.mcp.processor import CompletedFile

        # Directly add to completed to test cap
        for i in range(25):
            processor._completed.append(
                CompletedFile(file_id=str(i), filename=f"{i}.pdf", status="committed")
            )
        status = processor.get_status()
        assert len(status["completed"]) <= 20

    @pytest.mark.asyncio
    async def test_processing_completes_and_moves_to_completed(self, processor):
        await processor.add_document(processor._test_file)
        # Wait for processing to complete
        await asyncio.sleep(0.1)
        status = processor.get_status()
        assert status["current"] is None
        assert len(status["completed"]) == 1
        assert status["completed"][0]["status"] == "committed"

    @pytest.mark.asyncio
    async def test_failed_processing_captured(self, processor):
        processor.pipeline_callback = AsyncMock(side_effect=RuntimeError("boom"))
        await processor.add_document(processor._test_file)
        await asyncio.sleep(0.1)
        status = processor.get_status()
        assert status["current"] is None
        assert len(status["completed"]) == 1
        assert status["completed"][0]["status"] == "failed"
        assert "boom" in status["completed"][0]["error"]


class TestWaitForCompletion:
    """Tests for blocking wait_for_completion and helper methods."""

    @pytest.mark.asyncio
    async def test_wait_returns_on_completion(self, processor):
        result = await processor.add_document(processor._test_file)
        file_id = result["file_id"]
        wait_result = await processor.wait_for_completion(file_id, timeout=5)
        assert wait_result["status"] == "committed"
        assert wait_result["file_id"] == file_id

    @pytest.mark.asyncio
    async def test_wait_returns_on_failure(self, processor):
        processor.pipeline_callback = AsyncMock(side_effect=RuntimeError("fail"))
        result = await processor.add_document(processor._test_file)
        file_id = result["file_id"]
        wait_result = await processor.wait_for_completion(file_id, timeout=5)
        assert wait_result["status"] == "failed"
        assert "fail" in wait_result["error"]

    @pytest.mark.asyncio
    async def test_wait_timeout(self, processor):
        processor.pipeline_callback = AsyncMock(side_effect=lambda *a, **kw: asyncio.sleep(10))
        result = await processor.add_document(processor._test_file)
        file_id = result["file_id"]
        wait_result = await processor.wait_for_completion(file_id, timeout=0.1)
        assert wait_result["status"] == "timeout"
        processor.cancel()

    @pytest.mark.asyncio
    async def test_wait_for_already_completed(self, processor):
        result = await processor.add_document(processor._test_file)
        file_id = result["file_id"]
        # Wait for it to actually complete
        await asyncio.sleep(0.1)
        # Now wait again — should return immediately from completed list
        wait_result = await processor.wait_for_completion(file_id, timeout=1)
        assert wait_result["status"] == "committed"

    @pytest.mark.asyncio
    async def test_wait_for_unknown_file(self, processor):
        wait_result = await processor.wait_for_completion("nonexistent-id", timeout=1)
        assert wait_result["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_has_pending(self, processor):
        processor.pipeline_callback = AsyncMock(side_effect=lambda *a, **kw: asyncio.sleep(10))
        result = await processor.add_document(processor._test_file)
        file_id = result["file_id"]
        assert processor.has_pending(file_id) is True
        assert processor.has_pending("nonexistent") is False
        processor.cancel()

    @pytest.mark.asyncio
    async def test_add_document_with_wait(self, processor):
        result = await processor.add_document(processor._test_file, wait=True, wait_timeout=5)
        assert result["status"] == "committed"
        assert result["file_id"]

    @pytest.mark.asyncio
    async def test_add_document_without_wait(self, processor):
        result = await processor.add_document(processor._test_file, wait=False)
        assert result["status"] in ("processing", "queued")
        assert result["file_id"]
        # Wait for cleanup
        await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_get_completed(self, processor):
        result = await processor.add_document(processor._test_file)
        file_id = result["file_id"]
        await asyncio.sleep(0.1)
        completed = processor.get_completed(file_id)
        assert completed is not None
        assert completed["status"] == "committed"
        assert processor.get_completed("nonexistent") is None


class TestWorkerTaskSupervision:
    """The background worker task must surface crashes, not swallow them."""

    @pytest.mark.asyncio
    async def test_worker_task_crash_is_surfaced(self, processor, monkeypatch):
        """A crash in the worker loop must reach ``log_task_exception``.

        ``_start_worker`` spawns ``_worker_loop`` via ``asyncio.create_task``;
        without a done-callback an unhandled exception there vanishes with only
        a bare "Task exception was never retrieved" on stderr. The callback is
        the canonical surfacing path for every background task.
        """
        import chaoscypher_core.mcp.processor as processor_mod

        seen = []

        def _record(task):
            # Retrieve the exception so it is not reported as un-retrieved.
            seen.append(task.exception())

        monkeypatch.setattr(processor_mod, "log_task_exception", _record)

        async def _boom():
            raise RuntimeError("worker loop crashed")

        monkeypatch.setattr(processor, "_worker_loop", _boom)

        processor._start_worker()
        # Let the task run to completion and its done-callback fire.
        await asyncio.sleep(0.01)

        assert len(seen) == 1
        assert isinstance(seen[0], RuntimeError)
        assert "worker loop crashed" in str(seen[0])
