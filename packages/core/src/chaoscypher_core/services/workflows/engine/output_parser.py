# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow Output Manager - Output Extraction and Logging.

Handles workflow output processing:
1. Extract final outputs based on workflow schema
2. Map step outputs to schema properties
3. Apply defaults for missing properties
4. Log step outputs with structured summaries

Framework-agnostic component for the LangGraph workflow engine.
"""

from typing import Any, ClassVar, cast

import structlog


logger = structlog.get_logger(__name__)


class OutputManager:
    """Manages workflow output extraction and logging.

    Extracts final workflow outputs from step results based on
    the workflow's output_schema, with intelligent fallbacks and
    structured logging for debugging.

    Responsibilities:
    - Extract outputs matching workflow schema
    - Apply type-aware defaults for missing properties
    - Log step output summaries for debugging
    - Support schema-less workflows (return last step)

    Example:
        >>> manager = OutputManager()
        >>> workflow = {
        ...     'output_schema': {
        ...         'properties': {
        ...             'entities': {'type': 'array', 'default': []},
        ...             'summary': {'type': 'string'}
        ...         }
        ...     }
        ... }
        >>> step_outputs = {
        ...     'step_1': {'entities': [{'name': 'Alice'}]},
        ...     'step_2': {'summary': 'Analysis complete', 'count': 10}
        ... }
        >>> outputs = manager.extract_outputs(workflow, step_outputs)
        >>> outputs
        {'entities': [{'name': 'Alice'}], 'summary': 'Analysis complete'}

    """

    def extract_outputs(
        self, workflow: dict[str, Any], step_outputs: dict[str, Any]
    ) -> dict[str, Any]:
        """Extract final outputs from step outputs based on workflow schema.

        Uses workflow's output_schema to determine which properties to extract.
        Falls back to last step output if no schema defined.

        Extraction strategy:
        1. If no output_schema: Return last step output
        2. If no properties in schema: Return last step output
        3. Otherwise: Extract each schema property from step outputs

        For each property:
        - First checks last step output
        - Then searches all step outputs
        - Falls back to default value from schema
        - Falls back to type-aware empty value

        Args:
            workflow: Workflow configuration with optional output_schema
            step_outputs: Dictionary of step outputs {step_id: output_data}

        Returns:
            Final workflow outputs matching the output_schema

        Example:
            >>> # No schema - returns last step
            >>> workflow = {}
            >>> step_outputs = {'step_1': {'a': 1}, 'step_2': {'b': 2}}
            >>> manager.extract_outputs(workflow, step_outputs)
            {'b': 2}

            >>> # With schema - extracts specific properties
            >>> workflow = {
            ...     'output_schema': {
            ...         'properties': {
            ...             'result': {'type': 'string'},
            ...             'count': {'type': 'integer', 'default': 0}
            ...         }
            ...     }
            ... }
            >>> step_outputs = {
            ...     'step_1': {'result': 'success', 'temp': 'ignored'},
            ...     'step_2': {'other': 'data'}
            ... }
            >>> manager.extract_outputs(workflow, step_outputs)
            {'result': 'success', 'count': 0}  # Uses default for count

        Note:
            - Properties extracted in schema order
            - Searches all steps if not in last step
            - Type-aware defaults ([], {}, False, 0, None)

        """
        # Get output schema
        output_schema = workflow.get("output_schema")
        if not output_schema:
            # No schema - return last step output
            if step_outputs:
                last_step_id = list(step_outputs.keys())[-1]
                return cast("dict[str, Any]", step_outputs[last_step_id])
            return {}

        # Extract properties defined in output schema
        schema_properties = output_schema.get("properties", {})
        if not schema_properties:
            # No properties defined, return last step output
            if step_outputs:
                last_step_id = list(step_outputs.keys())[-1]
                return cast("dict[str, Any]", step_outputs[last_step_id])
            return {}

        # Build output by extracting schema properties from step outputs
        final_output = {}

        # Get last step output (most common case)
        last_step_output = {}
        if step_outputs:
            last_step_id = list(step_outputs.keys())[-1]
            last_step_output = step_outputs[last_step_id]

        # Extract each property defined in schema
        for prop_name, prop_schema in schema_properties.items():
            # First, try to find property in last step output
            if isinstance(last_step_output, dict) and prop_name in last_step_output:
                final_output[prop_name] = last_step_output[prop_name]
            else:
                # Search all step outputs for this property
                found = False
                for output in step_outputs.values():
                    if isinstance(output, dict) and prop_name in output:
                        final_output[prop_name] = output[prop_name]
                        found = True
                        break

                # If not found and property has a default, use it
                if not found and "default" in prop_schema:
                    final_output[prop_name] = prop_schema["default"]
                elif not found:
                    # Set to type-aware default
                    final_output[prop_name] = self._get_default_value(prop_schema)

        # Validate assembled output against the declared schema
        self._validate_against_schema(final_output, output_schema)
        return final_output

    @staticmethod
    def _validate_against_schema(
        final_output: dict[str, Any], output_schema: dict[str, Any]
    ) -> None:
        """Validate ``final_output`` against ``output_schema`` via jsonschema.

        Args:
            final_output: The assembled workflow output.
            output_schema: The workflow's declared output JSON Schema.

        Raises:
            SchemaValidationError: On any schema violation.

        """
        import jsonschema

        from chaoscypher_core.exceptions import SchemaValidationError

        try:
            jsonschema.validate(instance=final_output, schema=output_schema)
        except jsonschema.ValidationError as exc:
            path = [str(p) for p in exc.absolute_path]
            path_suffix = f" at '{'.'.join(path)}'" if path else ""
            raise SchemaValidationError(
                message=(f"Workflow output schema violation{path_suffix}: {exc.message}"),
                path=path,
            ) from exc

    # Declarative field mapping: (output_key, field_path, extraction_mode)
    # Modes: "len" = len(str(value)), "value" = raw value, "list_len" = len(list)
    _LOG_FIELD_MAP: ClassVar[list[tuple[str, tuple[str, ...], str]]] = [
        # Text fields — log string length
        ("content_length", ("content",), "len"),
        ("resolved_text_length", ("resolved_text",), "len"),
        ("value_length", ("value",), "len"),
        # Statistics — nested under "statistics"
        ("replacements_made", ("statistics", "replacements_made"), "value"),
        ("chains_found", ("statistics", "chains_found"), "value"),
        ("processing_time", ("statistics", "processing_time_ms"), "value"),
        # Control flow — raw values
        ("branch_taken", ("branch_taken",), "value"),
        ("result", ("result",), "value"),
        ("selected_index", ("index",), "value"),
        ("source", ("source",), "value"),
        # Chunks
        ("chunk_count", ("chunk_count",), "value"),
        ("chunks", ("chunks",), "list_len"),
        # Entities/relationships
        ("entities", ("entities",), "list_len"),
        ("relationships", ("relationships",), "list_len"),
    ]

    def log_step_output(self, step_name: str, step_output: dict[str, Any]) -> None:
        """Log key fields from step output for debugging and verification.

        Extracts and logs interesting fields based on common patterns,
        providing structured insights into step execution results.

        Args:
            step_name: Name of the step (for logging context)
            step_output: Output dictionary from the step

        Example:
            >>> manager = OutputManager()
            >>> step_output = {
            ...     'entities': [{'name': 'Alice'}, {'name': 'Bob'}],
            ...     'statistics': {'processing_time_ms': 150, 'replacements_made': 5},
            ...     'content': 'Long text...'
            ... }
            >>> manager.log_step_output('extract_entities', step_output)
            # Logs: step_output_summary(step_name='extract_entities',
            #         entities=2, processing_time=150, replacements_made=5, content_length=...)

        Note:
            - Only logs if interesting fields found
            - Uses DEBUG level for detailed output

        """
        output_summary: dict[str, int | str] = {}

        for log_key, path, mode in self._LOG_FIELD_MAP:
            # Navigate the nested path
            obj: Any = step_output
            for segment in path:
                if isinstance(obj, dict) and segment in obj:
                    obj = obj[segment]
                else:
                    obj = None
                    break

            if obj is None:
                continue

            if mode == "len":
                output_summary[log_key] = len(str(obj))
            elif mode == "list_len":
                output_summary[log_key] = len(obj) if isinstance(obj, list) else 0
            else:
                output_summary[log_key] = obj

        if output_summary:
            logger.debug("step_output_summary", step_name=step_name, **output_summary)

    @staticmethod
    def _get_default_value(prop_schema: dict[str, Any]) -> Any:
        """Get default value for a property based on its type.

        Args:
            prop_schema: Property schema with optional 'type' field

        Returns:
            Type-appropriate default value

        Example:
            >>> OutputManager._get_default_value({"type": "array"})
            []
            >>> OutputManager._get_default_value({"type": "integer"})
            0

        """
        prop_type = prop_schema.get("type", "string")

        if prop_type == "array":
            return []
        if prop_type == "object":
            return {}
        if prop_type == "boolean":
            return False
        if prop_type in ["integer", "number"]:
            return 0
        return None
