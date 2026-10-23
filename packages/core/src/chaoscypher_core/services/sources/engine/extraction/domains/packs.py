# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The packs a source extracts with: how a request names them, how a row stores them.

Every extraction gets the floor (``generic``); a source chooses the packs that
add to it (spec 2026-10-07 § 5). This module is the one definition of:

* **The label** (:func:`packs_label`): the packs sorted and joined with ``+``,
  or ``generic`` for none. One pack's label is its name, so every row,
  manifest and benchmark slot written before packs existed keeps its
  identity. ``sources.extraction_domain`` records it; :func:`packs_of_label`
  reads it back.
* **A request** (:func:`requested_packs`): what an API field, an MCP argument
  or a CLI option asks for. ``None`` (or ``"__auto__"``/``"auto"``) is
  "detect"; ``generic`` is the floor alone (``()``); a list is those packs.
  A single ``domain`` string is accepted as a one-element list.
* **A row** (:func:`forced_packs_of`, :func:`stored_packs`): the packs a
  stored row or a queue payload names, reading the JSON list columns first
  and the old single-name columns for rows written before them.
* **The columns** (:func:`pack_columns`): the list columns and the old
  single-name columns of one choice, kept in step for one release
  (``forced_domain``/``detected_domain`` hold the first pack or ``generic``,
  ``extraction_domain`` the label).

``None`` and ``()`` differ throughout: ``None`` is "not chosen" (detect),
``()`` is "the floor alone, chosen".

**Order.** A pack list keeps its order wherever it is stored and read: a
proposal's order is its score order, a request's the order it was given in,
and the first pack (what an old single-name column holds, and the one
domain object the content filter reads) is the list's first element. Only
:func:`packs_label` sorts, because an identity must not depend on order.

A parameter that still carries one domain name (the MCP pipeline, the SDK,
the CLI's upload ``domain``) carries the packs' label: one pack's label is
its name, so old callers are unchanged, and every reader of a stored or
passed name (:func:`packs_of_name`, :func:`pack_columns`, ``domain_stack``,
``DomainStack.from_domain_name``) reads a label. A queue payload carries
the list itself (``forced_packs``, built by :func:`pack_columns`) beside the
first pack in ``forced_domain``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.sources.engine.extraction.domains.core_types import FLOOR_NAME


PACKS_SEPARATOR: Final[str] = "+"
"""What joins the packs in a :func:`packs_label` (no registered name contains it)."""

AUTO_DETECT: Final[str] = "__auto__"
"""The request value meaning "detect the packs" (the UI's sentinel)."""

AUTO_TOKENS: Final[frozenset[str]] = frozenset({AUTO_DETECT, "auto"})
"""Every spelling of "detect": the UI's sentinel and the CLI's ``auto`` choice."""

MAX_PROPOSED_PACKS: Final[int] = 2
"""The most packs detection proposes (spec 2026-10-07 § 5: top two by score)."""


class InvalidPacksError(ValidationError, ValueError):
    """A pack choice that cannot be honoured (HTTP 400, ``field="packs"``).

    A malformed name (empty, the floor's, contains ``+``), both request
    fields naming packs, the auto sentinel mixed with packs, or a name the
    registry holds no pack for (:meth:`DomainStack.resolve`).

    Multiply inherits from ValidationError (so the HTTP error mapper
    produces a structured envelope) and ValueError (so the CLI, MCP and
    benchmark handlers that catch ``ValueError`` keep catching it).
    """

    def __init__(self, message: str) -> None:
        """Record ``message`` against the request's ``packs`` field."""
        super().__init__(message, field="packs")


def _checked(name: object, where: str) -> str:
    """``name`` as a pack name: a non-empty string that is not the floor and has no separator."""
    if not isinstance(name, str) or not name.strip():
        msg = f"{where}: a pack is a non-empty name, got {name!r}"
        raise InvalidPacksError(msg)
    name = name.strip()
    if name == FLOOR_NAME:
        msg = f"{where}: {FLOOR_NAME!r} is the floor every extraction gets; list only packs"
        raise InvalidPacksError(msg)
    if PACKS_SEPARATOR in name:
        msg = f"{where}: a pack name cannot contain {PACKS_SEPARATOR!r}, got {name!r}"
        raise InvalidPacksError(msg)
    return name


def normalized_packs(packs: Iterable[str], *, where: str = "packs") -> tuple[str, ...]:
    """``packs`` sorted, each once, every name checked (:class:`InvalidPacksError` on a bad one)."""
    return tuple(sorted({_checked(p, where) for p in packs}))


def packs_label(packs: Iterable[str]) -> str:
    """The string ``extraction_domain`` records for ``packs``: sorted, joined with ``+``; ``generic`` for none.

    >>> packs_label(["literary", "historical"])
    'historical+literary'
    >>> packs_label([])
    'generic'
    """
    return PACKS_SEPARATOR.join(normalized_packs(packs)) or FLOOR_NAME


def packs_of_label(label: str | None) -> tuple[str, ...]:
    """The packs a :func:`packs_label` names; ``()`` for None, empty or the floor's name.

    A recorded label is read back as written, so a hand-edited one in another
    order reads as the same packs.
    """
    if not label or label == FLOOR_NAME:
        return ()
    return normalized_packs(label.split(PACKS_SEPARATOR), where=f"label {label!r}")


def first_domain_name(packs: Sequence[str]) -> str:
    """The first of ``packs``, or ``generic`` for none: the one name an old single-name column holds."""
    return packs[0] if packs else FLOOR_NAME


def ordered_packs(values: Iterable[object]) -> tuple[str, ...]:
    """``values`` as pack names in their own order: each once (its first place), blanks and the floor dropped."""
    seen: dict[str, None] = {}
    for value in values:
        name = str(value).strip()
        if name and name != FLOOR_NAME:
            seen.setdefault(name, None)
    return tuple(seen)


def stored_name_packs(name: object) -> tuple[str, ...]:
    """The packs one stored name means: its label read leniently (the floor and blanks dropped).

    The reading of an old single-name column or a one-name caller
    (``forced_domain``, ``extraction_domain``, a resolver's ``domain_name``):
    a pack's name is that pack, a ``+``-joined label its packs in the order
    written, ``generic`` or nothing the floor alone. Never raises: what was
    stored is read as it is.
    """
    if not isinstance(name, str):
        return ()
    return ordered_packs(name.split(PACKS_SEPARATOR))


def packs_of_name(name: object) -> tuple[str, ...] | None:
    """The packs one passed or stored name forces, or ``None`` when it forces none.

    The one reading of a one-name value (a ``forced_domain`` column or
    argument, a proposal's ``detected_domain``, a CLI choice): empty, None
    or the auto sentinel force nothing; ``generic`` is the floor alone
    (``()``); a pack's name or a label is its packs (:func:`stored_name_packs`).
    """
    if not name or (isinstance(name, str) and name.strip() in AUTO_TOKENS):
        return None
    return stored_name_packs(name)


def _stored_list_packs(value: object) -> tuple[str, ...]:
    """The packs a stored JSON list holds, in its order, each once, the floor dropped."""
    if not isinstance(value, (list, tuple)):
        return ()
    return ordered_packs(value)


def requested_packs(
    packs: Iterable[str] | None = None,
    domain: str | None = None,
    *,
    where: str = "packs",
) -> tuple[str, ...] | None:
    """The packs a request forces, or ``None`` for "detect".

    The one reading of a surface's ``packs`` / ``domain`` field (API, MCP,
    CLI): ``packs`` is the list; ``domain`` is accepted as a one-element list.

    * ``None`` for both, an empty ``domain``, or the auto sentinel
      (``"__auto__"``, ``"auto"``) alone: ``None``, detection decides.
    * A list keeps the order it was given in (each pack once).
    * ``generic`` (alone or inside the list): the floor, which every
      extraction has, so it adds no pack; ``["generic"]`` is ``()``.
    * ``[]``: the floor alone, chosen.

    Raises:
        InvalidPacksError: When both fields name packs, the auto sentinel is mixed
            with packs, or a name is not a pack name (empty, contains ``+``).
    """
    domain = domain.strip() if isinstance(domain, str) else domain
    domain_given = bool(domain) and domain not in AUTO_TOKENS
    if packs is None:
        if not domain_given:
            return None
        values: list[str] = [str(domain)]
    else:
        values = [str(p).strip() for p in packs]
        if domain_given:
            msg = f"{where}: name the packs once, with 'packs' or 'domain', not both"
            raise InvalidPacksError(msg)
    auto = [v for v in values if v in AUTO_TOKENS]
    chosen = [v for v in values if v not in AUTO_TOKENS and v != FLOOR_NAME]
    if auto:
        if chosen or len(auto) != len(values):
            msg = f"{where}: auto-detect or a list of packs, not both"
            raise InvalidPacksError(msg)
        return None
    for name in chosen:
        _checked(name, where)
    return ordered_packs(chosen)


def forced_packs_of(row: Mapping[str, Any] | None) -> tuple[str, ...] | None:
    """The packs a row (source, job, queue payload) forces, or ``None`` when it forces none.

    Reads ``forced_packs`` (the JSON list) first; a row without it (written
    before the list existed, or a payload that names only the old key) is
    read from ``forced_domain``: empty or the auto sentinel is no forcing,
    ``generic`` is the floor alone, a name is that pack.
    """
    if not row:
        return None
    listed = row.get("forced_packs")
    if listed is not None:
        return _stored_list_packs(listed)
    return packs_of_name(row.get("forced_domain"))


def stored_packs(
    row: Mapping[str, Any] | None, *, list_key: str, name_key: str
) -> tuple[str, ...] | None:
    """The packs a row records in ``list_key``, else in the old ``name_key`` column; ``None`` for neither."""
    if not row:
        return None
    listed = row.get(list_key)
    if listed is not None:
        return _stored_list_packs(listed)
    name = row.get(name_key)
    if not name:
        return None
    return stored_name_packs(name)


def source_packs(row: Mapping[str, Any] | None) -> tuple[str, ...]:
    """The packs a source row extracts with (on the floor every source has).

    The forced packs when the row forces any (``forced_packs``, else the old
    ``forced_domain``), else the packs it was extracted with
    (``extraction_packs``, else the old ``extraction_domain`` read as a
    label); ``()`` for none, which is the floor alone.
    """
    forced = forced_packs_of(row)
    if forced is not None:
        return forced
    return stored_packs(row, list_key="extraction_packs", name_key="extraction_domain") or ()


def chosen_packs_of(row: Mapping[str, Any] | None) -> tuple[str, ...] | None:
    """The packs a user chose for a source row, or None when detection decides.

    The forced packs; else, for a row extracted under a user's choice
    (``extraction_domain_auto`` False, written before the forced columns
    were persisted at upload), the packs it was extracted with. What a retry
    or re-extract re-queues as the forced choice.
    """
    forced = forced_packs_of(row)
    if forced is not None or not row or row.get("extraction_domain_auto", True):
        return forced
    return stored_packs(row, list_key="extraction_packs", name_key="extraction_domain")


def proposal_packs(proposal: Mapping[str, Any] | None) -> list[str] | None:
    """The packs a proposal (or a detection result) names, in score order; ``None`` when it names none.

    Its ``packs``; a proposal stored before packs carries only
    ``detected_domain``, read as one (:func:`packs_of_name`). None when the
    proposal names neither (no proposal stored): a confirm then forces
    nothing and the analysis stage detects.
    """
    proposal = proposal or {}
    if proposal.get("packs") is not None:
        return list(_stored_list_packs(proposal["packs"]))
    named = packs_of_name(proposal.get("detected_domain"))
    return list(named) if named is not None else None


def detected_packs_of(result: Mapping[str, Any] | None) -> tuple[str, ...]:
    """The packs a detection result (``detect_extraction_domain``) extracts with, in its order; ``()`` for none."""
    return tuple(proposal_packs(result) or ())


def job_packs(job: Mapping[str, Any] | None) -> tuple[str, ...]:
    """The packs an extraction job extracts with: the forced ones, else the detected ones; ``()`` for neither."""
    forced = forced_packs_of(job)
    if forced is not None:
        return forced
    return stored_packs(job, list_key="detected_packs", name_key="detected_domain") or ()


def packs_choice(packs: Sequence[str] | None) -> str | None:
    """A pack choice as one name that keeps its order: the packs joined with ``+``, ``generic`` for none.

    What a one-name parameter carries (the MCP pipeline, the CLI's upload
    ``domain``, a prompt's offered choice); :func:`packs_of_name` reads it
    back in the same order. Not an identity (that is :func:`packs_label`,
    sorted). None stays None (not chosen).
    """
    if packs is None:
        return None
    return PACKS_SEPARATOR.join(ordered_packs(packs)) or FLOOR_NAME


def labels_of(packs: Sequence[str] | None) -> str | None:
    """The label of ``packs`` (:func:`packs_label`), or None for None (not chosen / not recorded)."""
    return packs_label(packs) if packs is not None else None


def job_pack_labels(job: Mapping[str, Any] | None) -> tuple[str | None, str | None]:
    """``(forced, detected)`` of a job as the one-name strings its finalize reads: each packs' label.

    The finalize paths thread a forced and a detected domain name through the
    one-name readers (the dedup and relation resolvers read a label,
    ``domain_stack``). One pack's label is its name and the floor's is
    ``generic``, so a single-pack job reads exactly its old columns; None
    stays None (not forced / not recorded).
    """
    detected = stored_packs(job, list_key="detected_packs", name_key="detected_domain")
    return labels_of(forced_packs_of(job)), labels_of(detected)


_PAIRS: Final[tuple[tuple[str, str, bool], ...]] = (
    # (list column, old single-name column, whether the name column holds the label)
    ("forced_packs", "forced_domain", False),
    ("extraction_packs", "extraction_domain", True),
    ("detected_packs", "detected_domain", False),
)


def pack_columns(values: Mapping[str, Any]) -> dict[str, Any]:
    """``values`` with each pack choice's list column and old single-name column kept in step.

    The one place the two representations are derived from each other, for
    one release (spec 2026-10-07 § 5). For each pair the list wins when both
    are present:

    * ``forced_packs`` / ``forced_domain`` (sources, jobs): the first pack or
      ``generic``; ``None`` (not forced) for both.
    * ``extraction_packs`` / ``extraction_domain`` (sources): the label
      (:func:`packs_label`), so the benchmark identity, ``snapshot_domain``
      and the comparability rule keep reading one string.
    * ``detected_packs`` / ``detected_domain`` (jobs): the first pack or
      ``generic``.

    A write that names only the old column (an older caller, or a one-name
    parameter carrying the packs' label) gets the list derived from it and
    the old column rewritten in its one-release form. Keys not in a pair pass
    through unchanged.
    """
    out = dict(values)
    for list_key, name_key, label in _PAIRS:
        if list_key in values:
            raw = values[list_key]
            if raw is None:
                out[name_key] = None
                continue
            packs = _stored_list_packs(raw)
            out[list_key] = list(packs)
            out[name_key] = packs_label(packs) if label else first_domain_name(packs)
        elif name_key in values:
            name = values[name_key]
            if not name or (list_key == "forced_packs" and name in AUTO_TOKENS):
                out[name_key] = None
                out[list_key] = None
            else:
                packs = stored_name_packs(name)
                out[list_key] = list(packs)
                out[name_key] = packs_label(packs) if label else first_domain_name(packs)
    return out


__all__ = [
    "AUTO_DETECT",
    "AUTO_TOKENS",
    "MAX_PROPOSED_PACKS",
    "PACKS_SEPARATOR",
    "InvalidPacksError",
    "chosen_packs_of",
    "detected_packs_of",
    "first_domain_name",
    "forced_packs_of",
    "job_pack_labels",
    "job_packs",
    "labels_of",
    "normalized_packs",
    "ordered_packs",
    "pack_columns",
    "packs_choice",
    "packs_label",
    "packs_of_label",
    "packs_of_name",
    "proposal_packs",
    "requested_packs",
    "source_packs",
    "stored_name_packs",
    "stored_packs",
]
