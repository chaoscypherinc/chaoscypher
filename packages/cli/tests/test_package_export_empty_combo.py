# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression test: graph package export must reject an all---no-* invocation.

Background (Bug): passing all four ``--no-*`` content flags produced an empty
content list that propagated into ``ExportManifest.validate_package_type``,
which raised a bare ``ValueError`` deep in the export pipeline. The user saw a
Python traceback rather than a clear error.

Fix: ``export()`` now raises ``click.UsageError`` before calling
``get_context()`` when every content flag is False.

This test pins that contract: non-zero exit, friendly message, no traceback.
"""

from __future__ import annotations

from click.testing import CliRunner

from chaoscypher_cli.commands.package.export import export


def test_export_with_all_content_flags_disabled_fails_cleanly():
    """All four --no-* flags must produce a clean UsageError, not a traceback."""
    result = CliRunner().invoke(
        export,
        [
            "--no-templates",
            "--no-knowledge",
            "--no-lenses",
            "--no-workflows",
        ],
    )
    assert result.exit_code != 0, (
        "Expected non-zero exit when all content types are disabled, "
        f"got exit_code={result.exit_code}. Output:\n{result.output}"
    )
    assert "at least one content type required" in result.output, (
        "Expected a friendly usage message in output. Got:\n{result.output}"
    )
    # The guard must fire before the pipeline, so no ExportManifest traceback.
    assert "ExportManifest" not in result.output
    assert "validate_package_type" not in result.output
