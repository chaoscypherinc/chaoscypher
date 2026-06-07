# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Trigger Executor - Event Dispatcher for Workflow Automation.

Executes workflows based on event-driven triggers.

Listens for events in the application (like node.create, file.upload, etc.)
and triggers workflows based on configured event triggers in the Workflow System.

Filter matching semantics
-------------------------
``_filters_match`` compares only top-level keys using literal Python equality
(``!=``). It does NOT support globs, regex, nested traversal, or type
coercion. Filter bounds are enforced at save time by TriggerService
(max depth 5, max 16 KB serialized, max 50 keys per level) to keep this
hot-path check O(n) in the top-level filter size.
"""

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import jsonschema
import structlog

from chaoscypher_core.services.events.bus import event_bus
from chaoscypher_core.services.workflows.triggers.management import TriggerStatsTracker
from chaoscypher_core.utils.id import generate_id


if TYPE_CHECKING:
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol
    from chaoscypher_core.ports.types import TriggerDict
    from chaoscypher_core.services.workflows.management.service import WorkflowService
    from chaoscypher_core.services.workflows.triggers.management.service import TriggerService

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class EventPublishResult:
    """Result of a sync event publish, so callers can react to backpressure.

    Attributes:
        published: True if the event was enqueued.
        dropped: True if the event was dropped due to backpressure.
        reason: Short machine-readable reason code when dropped (e.g. "queue_full").
    """

    published: bool
    dropped: bool
    reason: str | None = None


def _make_json_safe(obj: Any) -> Any:
    """Convert object to JSON-safe format, handling datetime and other non-serializable types."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_json_safe(item) for item in obj]
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    # For other types, try to convert to string
    return str(obj)


class TriggerExecutor:
    """Executes event-driven workflow triggers.

    Dispatches workflows based on configured event triggers in the system.
    """

    def __init__(
        self,
        trigger_service: TriggerService,
        workflow_service: WorkflowService,
        tool_service: Any,
        llm_service: Any,
        graph_repository: GraphRepositoryProtocol,
        search_repository: Any,
        database_name: str,
        execute_workflow_fn: Callable[..., Awaitable[dict[str, Any]]],
        trigger_history_limit: int = 100,
        event_queue_timeout: float = 1.0,
        event_queue_maxsize: int = 10_000,
        graph_manager: Any = None,
        discovery_service: Any = None,
    ) -> None:
        """Initialize the trigger service.

        Args:
            trigger_service: TriggerService instance for accessing triggers
            workflow_service: WorkflowService instance for accessing workflows
            tool_service: ToolService for tool execution
            llm_service: LLM service for AI operations
            graph_repository: GraphRepository for graph operations
            search_repository: SearchRepository for search operations
            database_name: Current database name
            execute_workflow_fn: Async callable for workflow execution (injected to avoid
                circular dependency on neuron/cortex packages)
            trigger_history_limit: Maximum number of execution records to keep per trigger
            event_queue_timeout: Seconds to wait for events before re-checking loop condition
            event_queue_maxsize: Bound on pending events; once full, publish_event_sync
                drops events (and increments events_dropped_total) instead of growing
                without limit. Guards against unbounded memory growth if the consumer
                stalls or falls behind during a large import.
            graph_manager: GraphRepository instance for checking node embeddings (optional)
            discovery_service: DiscoveryService for AI analysis (optional, removed)

        """
        self.trigger_service = trigger_service
        self.workflow_service = workflow_service
        self.tool_service = tool_service
        self.llm_service = llm_service
        self.graph_repository = graph_repository
        self.search_repository = search_repository
        self.discovery_service = discovery_service
        self.database_name = database_name
        self.execute_workflow_fn = execute_workflow_fn
        self.graph_manager = graph_manager
        self._event_queue_timeout = event_queue_timeout
        self.event_queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=event_queue_maxsize)
        self._process_task: asyncio.Task[None] | None = None
        self.is_running = False
        self.events_dropped_total = 0
        self.stats_tracker = TriggerStatsTracker(history_limit=trigger_history_limit)
        logger.info(
            "trigger_dispatcher_initialized",
            history_limit=trigger_history_limit,
            database_name=database_name,
        )

    async def start(self) -> None:
        """Start the trigger service event loop."""
        if self.is_running:
            logger.warning("trigger_dispatcher_already_running")
            return

        self.is_running = True
        logger.info("trigger_dispatcher_started")

        # Start event processing loop and store task reference
        self._process_task = asyncio.create_task(self._process_events())

    async def stop(self) -> None:
        """Stop the trigger service event loop and cancel its task."""
        self.is_running = False
        if self._process_task is not None:
            self._process_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._process_task
            self._process_task = None
        logger.info("trigger_dispatcher_stopped")

    async def _process_events(self) -> None:
        """Process events from the queue."""
        while self.is_running:
            try:
                # Wait for an event with timeout to allow checking is_running
                try:
                    event = await asyncio.wait_for(
                        self.event_queue.get(), timeout=self._event_queue_timeout
                    )
                except TimeoutError:
                    continue

                # Process the event
                await self._handle_event(event)

            except Exception as e:
                logger.exception(
                    "trigger_event_processing_failed",
                    error_type=type(e).__name__,
                    error_message=str(e),
                )

    async def _handle_event(self, event: dict[str, Any]) -> None:
        """Handle a single event by finding matching triggers and executing workflows.

        Args:
            event: Event dictionary with 'source' and 'data' keys

        """
        event_source = event.get("source")
        event_data = event.get("data", {})

        logger.debug("trigger_handling_event", event_source=event_source)

        # Get all enabled triggers for this event source
        triggers = self.trigger_service.list_triggers(event_source=event_source, enabled=True)

        if not triggers:
            logger.debug("trigger_no_triggers_found", event_source=event_source)
            return

        logger.info(
            "trigger_found_enabled_triggers", event_source=event_source, trigger_count=len(triggers)
        )

        # Execute workflows for each matching trigger
        for trigger in triggers:
            try:
                # Check if trigger filters match event data
                trigger_filters = trigger.get("filters", {})
                if not self._filters_match(trigger_filters, event_data):
                    # Distinguish "filter mentions a key the event payload
                    # doesn't carry" (silent-fail vector — operator-visible
                    # WARNING) from "key matches but value differs" (normal
                    # filtering — DEBUG). See _filter_has_unknown_keys.
                    unknown_keys = self._filter_has_unknown_keys(trigger_filters, event_data)
                    if unknown_keys:
                        logger.warning(
                            "trigger_filter_unknown_keys",
                            trigger_name=trigger["name"],
                            trigger_id=trigger["id"],
                            event_source=event_source,
                            unknown_keys=sorted(unknown_keys),
                            event_payload_keys=sorted(event_data.keys()),
                            hint=(
                                "Filter mentions key(s) not present in event "
                                "payload — trigger can never fire on this event "
                                "source. Check the publisher's payload schema "
                                "and update or remove the filter."
                            ),
                        )
                    else:
                        logger.debug(
                            "trigger_filters_not_matched",
                            trigger_name=trigger["name"],
                            trigger_id=trigger["id"],
                        )
                    continue

                is_auto_embed = trigger["workflow_id"] == "system_workflow_generate_embeddings_v1"

                if self._should_skip_auto_embed(is_auto_embed, event_data):
                    continue

                if not is_auto_embed:
                    event_bus.emit(
                        "trigger_fired",
                        action=f"Trigger fired: {trigger['name']}",
                        source="trigger",
                        details={
                            "trigger_id": trigger["id"],
                            "event_source": str(event_source) if event_source else None,
                        },
                    )

                if is_auto_embed:
                    logger.debug(
                        "trigger_matched_async",
                        trigger_name=trigger["name"],
                        trigger_id=trigger["id"],
                        event_source=event_source,
                    )
                else:
                    logger.info(
                        "trigger_matched",
                        trigger_name=trigger["name"],
                        trigger_id=trigger["id"],
                        event_source=event_source,
                    )

                await self._dispatch_trigger_workflow(
                    trigger=trigger,
                    event_data=event_data,
                    event_source=event_source,
                    is_auto_embed=is_auto_embed,
                )

            except Exception as e:
                logger.exception(
                    "trigger_processing_failed",
                    trigger_name=trigger.get("name", "unknown"),
                    trigger_id=trigger.get("id"),
                    error_type=type(e).__name__,
                    error_message=str(e),
                )

    def _should_skip_auto_embed(
        self,
        is_auto_embed: bool,
        event_data: dict[str, Any],
    ) -> bool:
        """Check whether an auto-embedding trigger should be skipped.

        For auto-embedding triggers, checks if the target node entity
        already has embeddings and can be skipped to avoid redundant work.

        Args:
            is_auto_embed: Whether this is an auto-embedding trigger.
            event_data: Event data with optional ``entity_type`` and ``entity_id``.

        Returns:
            True if the trigger should be skipped, False otherwise.

        """
        if not is_auto_embed or not self.graph_manager:
            return False

        entity_type = event_data.get("entity_type")
        entity_id = event_data.get("entity_id")

        if entity_type != "node" or not entity_id:
            return False

        try:
            node = self.graph_manager.get_node(entity_id)
            if node and node.embedding:
                logger.debug("trigger_skipping_embedding_already_present", node_id=entity_id)
                return True
        except Exception as e:
            logger.warning(
                "trigger_embedding_status_check_failed",
                node_id=entity_id,
                error_type=type(e).__name__,
                error_message=str(e),
            )
        return False

    async def _dispatch_trigger_workflow(
        self,
        trigger: TriggerDict,
        event_data: dict[str, Any],
        event_source: Any,
        is_auto_embed: bool,
    ) -> None:
        """Execute or fire-and-forget a triggered workflow and record stats.

        For auto-embedding triggers, dispatches asynchronously to avoid
        blocking. For all other triggers, executes synchronously and
        records success/failure statistics.

        Args:
            trigger: Trigger dict with ``workflow_id``, ``name``, ``id``,
                and optional ``workflow_inputs``.
            event_data: Event data to merge with trigger inputs.
            event_source: Original event source for stats recording.
            is_auto_embed: Whether this is an auto-embedding trigger.

        """
        start_time = time.time()
        workflow_inputs = trigger.get("workflow_inputs") or {}
        merged_inputs = {**event_data, **workflow_inputs}

        # Validate merged inputs against the target workflow's input_schema.
        # A validation failure MUST NOT raise — it would break the dispatch
        # loop for sibling triggers. We log, record a failed stats row, and
        # skip this one trigger.
        workflow = self.workflow_service.get_workflow(trigger["workflow_id"])
        input_schema = workflow.get("input_schema") if workflow else None
        if input_schema:
            try:
                jsonschema.validate(instance=merged_inputs, schema=input_schema)
            except jsonschema.ValidationError as validation_error:
                execution_time = time.time() - start_time
                workflow_name = workflow["name"] if workflow else trigger["workflow_id"]
                event_source_val = str(event_source) if event_source is not None else "unknown"
                self.stats_tracker.record_execution(
                    execution_id=generate_id(),
                    trigger_id=trigger["id"],
                    trigger_name=trigger["name"],
                    workflow_id=trigger["workflow_id"],
                    workflow_name=workflow_name,
                    event_source=event_source_val,
                    success=False,
                    execution_time=execution_time,
                    error=f"input_schema validation failed: {validation_error.message}",
                )
                logger.warning(
                    "trigger_input_schema_validation_failed",
                    trigger_id=trigger["id"],
                    trigger_name=trigger["name"],
                    workflow_id=trigger["workflow_id"],
                    error_message=validation_error.message,
                    error_path=list(validation_error.absolute_path),
                )
                return  # skip dispatch — do NOT raise

        # For auto-embedding, fire-and-forget to prevent blocking
        if is_auto_embed:
            event_source_val = str(event_source) if event_source is not None else "unknown"
            task = asyncio.create_task(
                self._execute_workflow_async(
                    trigger=trigger,
                    merged_inputs=merged_inputs,
                    event_source=event_source_val,
                    start_time=start_time,
                )
            )
            if not hasattr(self, "_background_tasks"):
                self._background_tasks = set()
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
            return

        try:
            result = await self.execute_workflow_fn(
                workflow_id=trigger["workflow_id"],
                inputs=merged_inputs,
                workflow_service=self.workflow_service,
                tool_service=self.tool_service,
                llm_service=self.llm_service,
                graph_repository=self.graph_repository,
                search_repository=self.search_repository,
                discovery_service=self.discovery_service,
                database_name=self.database_name,
                triggered_by="trigger",
                trigger_id=trigger["id"],
            )

            execution_time = time.time() - start_time
            workflow_name = workflow["name"] if workflow else trigger["workflow_id"]

            event_source_val = str(event_source) if event_source is not None else "unknown"
            self.stats_tracker.record_execution(
                execution_id=result.get("execution_id", generate_id()),
                trigger_id=trigger["id"],
                trigger_name=trigger["name"],
                workflow_id=trigger["workflow_id"],
                workflow_name=workflow_name,
                event_source=event_source_val,
                success=True,
                execution_time=execution_time,
            )

            logger.info(
                "trigger_workflow_executed",
                workflow_name=workflow_name,
                workflow_id=trigger["workflow_id"],
                trigger_name=trigger["name"],
                trigger_id=trigger["id"],
                execution_time=round(execution_time, 2),
                success=True,
            )

        except Exception as workflow_error:
            execution_time = time.time() - start_time
            workflow_name = workflow["name"] if workflow else trigger["workflow_id"]

            event_source_val = str(event_source) if event_source is not None else "unknown"
            self.stats_tracker.record_execution(
                execution_id=generate_id(),
                trigger_id=trigger["id"],
                trigger_name=trigger["name"],
                workflow_id=trigger["workflow_id"],
                workflow_name=workflow_name,
                event_source=event_source_val,
                success=False,
                execution_time=execution_time,
                error=str(workflow_error),
            )

            logger.exception(
                "trigger_workflow_execution_failed",
                workflow_name=workflow_name,
                workflow_id=trigger["workflow_id"],
                trigger_name=trigger["name"],
                trigger_id=trigger["id"],
                error_type=type(workflow_error).__name__,
                error_message=str(workflow_error),
                execution_time=round(execution_time, 2),
            )

    async def _execute_workflow_async(
        self,
        trigger: TriggerDict,
        merged_inputs: dict[str, Any],
        event_source: str,
        start_time: float,
    ) -> None:
        """Execute a workflow asynchronously without blocking (for auto-embedding).

        Records stats after completion.
        """
        try:
            result = await self.execute_workflow_fn(
                workflow_id=trigger["workflow_id"],
                inputs=merged_inputs,
                workflow_service=self.workflow_service,
                tool_service=self.tool_service,
                llm_service=self.llm_service,
                graph_repository=self.graph_repository,
                search_repository=self.search_repository,
                discovery_service=self.discovery_service,
                database_name=self.database_name,
                triggered_by="trigger",
                trigger_id=trigger["id"],
            )

            execution_time = time.time() - start_time

            # Get workflow name
            workflow = self.workflow_service.get_workflow(trigger["workflow_id"])
            workflow_name = workflow["name"] if workflow else trigger["workflow_id"]

            # Record successful execution
            self.stats_tracker.record_execution(
                execution_id=result.get("execution_id", generate_id()),
                trigger_id=trigger["id"],
                trigger_name=trigger["name"],
                workflow_id=trigger["workflow_id"],
                workflow_name=workflow_name,
                event_source=event_source,
                success=True,
                execution_time=execution_time,
            )

            logger.debug(
                "async_workflow_completed",
                workflow_name=workflow_name,
                execution_time=round(execution_time, 2),
            )

        except Exception as workflow_error:
            execution_time = time.time() - start_time

            # Get workflow name for error logging
            workflow = self.workflow_service.get_workflow(trigger["workflow_id"])
            workflow_name = workflow["name"] if workflow else trigger["workflow_id"]

            # Record failed execution
            self.stats_tracker.record_execution(
                execution_id=generate_id(),
                trigger_id=trigger["id"],
                trigger_name=trigger["name"],
                workflow_id=trigger["workflow_id"],
                workflow_name=workflow_name,
                event_source=event_source,
                success=False,
                execution_time=execution_time,
                error=str(workflow_error),
            )

            logger.debug(
                "async_workflow_failed",
                workflow_name=workflow_name,
                error_type=type(workflow_error).__name__,
                error_message=str(workflow_error),
            )

    def _filters_match(self, filters: dict[str, Any], event_data: dict[str, Any]) -> bool:
        """Check event data against trigger filters via top-level literal equality.

        Filter keys must equal event_data keys exactly, and values must
        compare equal with ``==``. See module docstring for semantics.

        Args:
            filters: Trigger filter criteria
            event_data: Event data to check

        Returns:
            True if all filters match, False otherwise

        """
        if not filters:
            return True  # No filters means match everything

        for key, expected_value in filters.items():
            actual_value = event_data.get(key)
            if actual_value != expected_value:
                return False

        return True

    def _filter_has_unknown_keys(
        self, filters: dict[str, Any], event_data: dict[str, Any]
    ) -> set[str]:
        """Return filter keys that are absent from the event payload entirely.

        A filter key absent from ``event_data`` can never match — the trigger
        is structurally guaranteed never to fire on these events. Distinct
        from a value mismatch (the key is present, the value differs), which
        is normal filtering ("I only want events where X=Y").

        Used by ``_handle_event`` to surface silent-fail vectors at WARNING
        level so misconfigured triggers don't sit in the registry forever
        looking healthy while never firing. ``_validate_filters`` does not
        catch this today because there is no event-source schema registry
        (see `the public issue tracker` § P2 Reliability for the registry-based
        validation that would reject these at trigger creation time).

        Args:
            filters: Trigger filter criteria.
            event_data: Event payload received by ``_handle_event``.

        Returns:
            Set of filter keys absent from ``event_data``. Empty when every
            filter key is present (a non-match in that case is just a
            value mismatch — normal usage).
        """
        return {k for k in filters if k not in event_data}

    def publish_event_sync(
        self, event_source: str, event_data: dict[str, Any]
    ) -> EventPublishResult:
        """Publish an event synchronously and report backpressure.

        Args:
            event_source: Source identifier (e.g., 'node.created').
            event_data: Event payload data.

        Returns:
            EventPublishResult describing success or drop reason.
            Fire-and-forget callers can ignore; delivery-critical callers
            can inspect ``.dropped`` / ``.reason`` and react.
        """
        safe_event_data = _make_json_safe(event_data)

        event = {
            "source": event_source,
            "data": safe_event_data,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        try:
            self.event_queue.put_nowait(event)
        except asyncio.QueueFull:
            self.events_dropped_total += 1
            logger.warning(
                "event_queue_full_dropped",
                event_source=event_source,
                events_dropped_total=self.events_dropped_total,
            )
            return EventPublishResult(published=False, dropped=True, reason="queue_full")

        logger.debug("event_published_sync", event_source=event_source)
        return EventPublishResult(published=True, dropped=False, reason=None)
