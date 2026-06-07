# packages/core/src/chaoscypher_core/utils/settings_validators.py
# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pydantic validators that resolve their constraint from runtime settings.

Used for fields whose maximum length is operator-tunable (e.g. chat content,
pause reason). Hardcoded security/protocol caps live in
``chaoscypher_core.policy`` instead.

Usage:

    from typing import Annotated
    from pydantic import BaseModel
    from chaoscypher_core.utils.settings_validators import max_length_from_settings

    class ChatMessage(BaseModel):
        content: Annotated[
            str,
            max_length_from_settings("chat_context.chat_message_max_length"),
        ]

The validator does an O(depth) attribute walk per validation, where depth
is bounded by the hand-written setting path (typically <= 3). The
``get_settings()`` call is ``lru_cache``'d, so amortized to O(1) after
first use.
"""

from __future__ import annotations

from typing import Any

from pydantic import BeforeValidator

from chaoscypher_core.app_config import get_settings


def max_length_from_settings(setting_path: str) -> BeforeValidator:
    """Return a Pydantic ``BeforeValidator`` that enforces a settings-driven max length.

    The validator runs ``get_settings()``, walks the dotted ``setting_path``,
    and rejects strings whose length exceeds the resolved value. Non-strings
    pass through untouched (Pydantic's normal coercion handles them).

    Args:
        setting_path: dotted path under the global ``Settings`` object,
            e.g. ``"chat_context.chat_message_max_length"``.

    Returns:
        A ``BeforeValidator`` instance suitable for use in
        ``Annotated[str, ...]`` field declarations.

    Raises:
        ValueError (during validation): when the string exceeds the
            configured limit. Pydantic wraps this in ``ValidationError``.
    """

    def _resolve(settings: Any) -> int:
        node: Any = settings
        try:
            for part in setting_path.split("."):
                node = getattr(node, part)
        except AttributeError as e:
            msg = (
                f"Settings path {setting_path!r} could not be resolved: {e}. "
                "Check the dotted path against the Settings model."
            )
            raise ValueError(msg) from e
        if node is None:
            msg = f"Settings path {setting_path!r} resolved to None (expected an int)."
            raise ValueError(msg)
        try:
            return int(node)
        except (TypeError, ValueError) as e:
            msg = f"Settings path {setting_path!r} resolved to non-int value {node!r}: {e}"
            raise ValueError(msg) from e

    def _validator(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        max_len = _resolve(get_settings())
        if len(value) > max_len:
            msg = (
                f"String length {len(value)} exceeds configured maximum "
                f"{max_len} (setting: {setting_path})"
            )
            raise ValueError(msg)
        return value

    return BeforeValidator(_validator)


__all__ = ["max_length_from_settings"]
