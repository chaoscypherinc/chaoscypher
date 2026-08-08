# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Interpolation of values nested inside lists.

Regression: dicts (and lists) inside list values were passed through
verbatim, so configurations like ``{"messages": [{"content": "{{inputs.x}}"}]}``
kept the literal template — despite the class docstring promising nested
dict/list support.
"""

from chaoscypher_core.services.workflows.engine.interpolator import ParameterInterpolator


CONTEXT = {"inputs": {"x": "VALUE", "n": 7}, "steps": {"s1": {"out": "S1OUT"}}}


def test_dict_inside_list_is_interpolated() -> None:
    params = {"messages": [{"role": "user", "content": "{{inputs.x}}"}]}
    result = ParameterInterpolator.interpolate_parameters(params, CONTEXT)
    assert result == {"messages": [{"role": "user", "content": "VALUE"}]}


def test_list_inside_list_is_interpolated() -> None:
    params = {"matrix": [["{{inputs.x}}", "literal"], ["{{steps.s1.out}}"]]}
    result = ParameterInterpolator.interpolate_parameters(params, CONTEXT)
    assert result == {"matrix": [["VALUE", "literal"], ["S1OUT"]]}


def test_type_preserved_for_full_reference_in_nested_dict() -> None:
    params = {"items": [{"count": "{{inputs.n}}"}]}
    result = ParameterInterpolator.interpolate_parameters(params, CONTEXT)
    assert result == {"items": [{"count": 7}]}


def test_plain_string_items_still_interpolated() -> None:
    params = {"tags": ["{{inputs.x}}", "keep"]}
    result = ParameterInterpolator.interpolate_parameters(params, CONTEXT)
    assert result == {"tags": ["VALUE", "keep"]}


def test_scalars_untouched() -> None:
    params = {"nums": [1, 2.5, None, True]}
    result = ParameterInterpolator.interpolate_parameters(params, CONTEXT)
    assert result == {"nums": [1, 2.5, None, True]}
