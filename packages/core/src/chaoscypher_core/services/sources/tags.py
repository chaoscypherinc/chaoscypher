# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The one tag rule; every surface validates through here.

Document ("source") tags are normalised by trimming and lower-casing, then
must match :data:`TAG_NAME_PATTERN`. A tag that does not match is refused,
never silently changed. Duplicates after normalising are dropped, keeping
first-seen order, and a document carries at most
:data:`~chaoscypher_core.policy.TAGS_PER_SOURCE_MAX` tags.

MCP, REST (and the UI through it), the CLI and package import all call
:func:`validate_tag` / :func:`validate_tags` / :func:`ensure_tag_count`, or
use the :data:`TagName` / :data:`TagNames` Pydantic types, which wrap them.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any

import structlog
from pydantic import BeforeValidator
from pydantic_core import PydanticCustomError
from sqlalchemy.exc import IntegrityError, PendingRollbackError

from chaoscypher_core.exceptions import ConflictError, NotFoundError, ValidationError
from chaoscypher_core.models import TagMatch, is_memory_source
from chaoscypher_core.policy import TAG_NAME_MAX_LENGTH, TAGS_PER_SOURCE_MAX
from chaoscypher_core.utils.id import generate_id
from chaoscypher_core.utils.tool_failure import discard_failed_work


if TYPE_CHECKING:
    from typing import Protocol

    from chaoscypher_core.ports.storage_source_tags import SourceTagStorageProtocol
    from chaoscypher_core.ports.storage_sources import SourceStorageProtocol
    from chaoscypher_core.ports.transactional import TransactionalAdapterProtocol

    class SourceAndTagStorage(
        SourceStorageProtocol,
        SourceTagStorageProtocol,
        TransactionalAdapterProtocol,
        Protocol,
    ):
        """A storage holding sources and their tags, with a transaction (the SQLite adapter)."""


__all__ = [
    "REPLACE_WITH_ADD_OR_REMOVE",
    "TAG_NAME_PATTERN",
    "TagMatch",
    "TagName",
    "TagNames",
    "TagRuleError",
    "as_pydantic_error",
    "check_tag_edit",
    "check_tag_list",
    "edit_source_tags",
    "ensure_tag_count",
    "filter_tag_scope",
    "find_tags",
    "get_or_create_tag",
    "get_or_create_tags",
    "normalize_filter_tags",
    "normalize_tag",
    "require_taggable_source",
    "source_tag_names",
    "validate_tag",
    "validate_tags",
]

logger = structlog.get_logger(__name__)


TAG_NAME_PATTERN: re.Pattern[str] = re.compile(
    rf"^[a-z0-9][a-z0-9:_-]{{0,{TAG_NAME_MAX_LENGTH - 1}}}$"
)


class TagRuleError(ValidationError):
    """A tag, or a document's tag list, breaks the tag rule.

    Always reported against the ``tags`` field, so it maps to HTTP 400 like
    any other :class:`~chaoscypher_core.exceptions.ValidationError`.
    """

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        """Initialize the instance.

        Args:
            message: The rule's refusal, shown to the user as-is.
            details: Additional context about the refusal.

        """
        super().__init__(message, field="tags", details=details)


def normalize_tag(raw: str) -> str:
    """Return ``raw`` trimmed and lower-cased (no other change is ever made).

    Only ASCII survives the rule: :func:`validate_tag` refuses a tag whose
    trimmed form is not ASCII, so lower-casing can never turn one character
    into another (the Kelvin sign into ``k``, say) without the user seeing it.
    """
    return raw.strip().lower()


def validate_tag(raw: object) -> str:
    """Return the normalised tag, or raise :class:`TagRuleError`.

    Args:
        raw: The tag as given; anything that is not a ``str`` is refused.

    Returns:
        The normalised tag name.

    Raises:
        TagRuleError: ``raw`` is not text, its trimmed form is not ASCII, or
            its normalised form does not match :data:`TAG_NAME_PATTERN`.

    """
    if not isinstance(raw, str):
        msg = f"a tag is text, not {type(raw).__name__}"
        raise TagRuleError(msg)
    name = normalize_tag(raw)
    if not raw.strip().isascii() or not TAG_NAME_PATTERN.fullmatch(name):
        msg = (
            f"tag \"{raw.strip()}\" is not allowed: use a-z, 0-9, ':', '_' or '-', "
            f"starting with a letter or digit, at most {TAG_NAME_MAX_LENGTH} characters"
        )
        raise TagRuleError(msg)
    return name


def ensure_tag_count(count: int) -> None:
    """Refuse a document tag list of ``count`` tags if it is over the cap.

    Raises:
        TagRuleError: ``count`` exceeds ``TAGS_PER_SOURCE_MAX``.

    """
    if count > TAGS_PER_SOURCE_MAX:
        msg = f"a document can carry at most {TAGS_PER_SOURCE_MAX} tags; this would give it {count}"
        raise TagRuleError(msg)


def check_tag_list(raw: object) -> Iterable[object]:
    """Return ``raw`` if it is a list (or other iterable) of tags, else refuse it.

    A bare string, bytes, a mapping, ``None`` or a non-iterable is refused
    rather than iterated, so ``"work"`` never becomes four tags.

    Raises:
        TagRuleError: ``raw`` is not a list of tags.

    """
    if isinstance(raw, str | bytes | bytearray | Mapping) or not isinstance(raw, Iterable):
        msg = f"tags are a list of text, not {type(raw).__name__}"
        raise TagRuleError(msg)
    return raw


def validate_tags(raw: Iterable[object]) -> list[str]:
    """Validate each tag, drop duplicates (first-seen order), then check the cap.

    Args:
        raw: The tags as given: a list (or other iterable) of text. A bare
            string, bytes, a mapping, ``None`` or a non-iterable is refused
            rather than iterated, so ``"work"`` never becomes four tags.

    Returns:
        The normalised, de-duplicated tag names.

    Raises:
        TagRuleError: ``raw`` is not a list of text, a tag breaks the rule,
            or the result is over the cap.

    """
    result = list(dict.fromkeys(validate_tag(tag) for tag in check_tag_list(raw)))
    ensure_tag_count(len(result))
    return result


def normalize_filter_tags(raw: Iterable[object]) -> list[str]:
    """Validate and normalise the tags of a filter (a listing's or a search's scope).

    Like :func:`validate_tags` without the per-document cap: a filter may name
    more tags than one document can carry.

    Raises:
        TagRuleError: ``raw`` is not a list of text, or a tag breaks the rule.

    """
    return list(dict.fromkeys(validate_tag(tag) for tag in check_tag_list(raw)))


def filter_tag_scope(raw: Iterable[object], match: str) -> tuple[list[str], TagMatch]:
    """A search's tag scope as given: its tags (:func:`normalize_filter_tags`) and ``match``.

    Raises:
        TagRuleError: a tag breaks the rule, or ``match`` is neither ``any`` nor ``all``.

    """
    tags = normalize_filter_tags(raw)
    try:
        return tags, TagMatch(match)
    except ValueError as exc:
        msg = f'match is "any" or "all", not "{match}"'
        raise TagRuleError(msg) from exc


def as_pydantic_error[R](check: Callable[[Any], R]) -> Callable[[Any], R]:
    """Wrap a rule check so its refusal reaches Pydantic with the rule's words.

    A :class:`TagRuleError` becomes ``PydanticCustomError("tag_rule", ...)``
    whose message is the refusal verbatim, so a request model reports the
    rule's exact text (HTTP 422). :data:`TagName` / :data:`TagNames` use it as
    a *before* validator, so the check sees the input exactly as given:
    ``b"work"`` or ``"work"`` for a list is refused by the rule itself instead
    of being coerced or iterated by Pydantic first. A request model's own
    validator uses it to run a rule check such as :func:`check_tag_edit`.
    """

    def run(value: Any) -> R:
        try:
            return check(value)
        except TagRuleError as exc:
            # The refusal goes in as context, not as the template, so a
            # brace in the tag as given is never read as a placeholder.
            raise PydanticCustomError("tag_rule", "{reason}", {"reason": exc.message}) from exc

    return run


TagName = Annotated[str, BeforeValidator(as_pydantic_error(validate_tag))]
"""A single tag field on a Pydantic model, normalised and checked by the rule."""

TagNames = Annotated[list[str], BeforeValidator(as_pydantic_error(validate_tags))]
"""A document tag-list field on a Pydantic model, normalised, de-duplicated and capped."""


# ----------------------------------------------------------------------
# The one name lookup, and the edits built on it. Every caller that has a
# tag's name (MCP, REST, the CLI, package import) finds or creates the tag
# here; nothing else lists every tag to match a name by hand.
# ----------------------------------------------------------------------

REPLACE_WITH_ADD_OR_REMOVE = "replace sets the whole list; it cannot be combined with add or remove"
"""The refusal when ``replace`` is given together with ``add`` or ``remove``."""


def check_tag_edit(
    *,
    add: Sequence[str] = (),
    remove: Sequence[str] = (),
    replace: Sequence[str] | None = None,
) -> None:
    """Refuse an edit that is not one kind: ``replace`` with ``add`` or ``remove``.

    The one copy of this rule: :func:`edit_source_tags` runs it, and a request
    model (REST's ``SourceTagsEdit``) runs it through :func:`as_pydantic_error`.

    Raises:
        TagRuleError: ``replace`` is given together with ``add`` or ``remove``
            (:data:`REPLACE_WITH_ADD_OR_REMOVE`).

    """
    if replace is not None and (add or remove):
        raise TagRuleError(REPLACE_WITH_ADD_OR_REMOVE)


def _create_tag(
    storage: SourceTagStorageProtocol,
    database_name: str,
    name: str,
    *,
    color: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Create tag ``name`` (already normalised), or return the one a concurrent writer made.

    The unique index on ``(database_name, name)`` refuses the second of two
    creates that race; the loser rolls its session back and reads the winner's.
    """
    try:
        return storage.create_tag(
            {
                "id": generate_id("tag"),
                "database_name": database_name,
                "name": name,
                "color": color,
                "description": description,
                "created_at": datetime.now(UTC),
            }
        )
    except IntegrityError as exc:
        discard_failed_work(getattr(storage, "session", None))
        try:
            existing = storage.get_tags_by_names(database_name, [name])
        except PendingRollbackError:
            # Inside an enclosing transaction the failed write cannot be undone
            # on its own; the whole block rolls back as this goes on, and a
            # retry finds the winner's tag. (An edit holds the writer lock, so
            # another process cannot win there; this is the same-session case.)
            msg = f'another writer created tag "{name}" at the same time; try again'
            raise ConflictError(msg, details={"name": name}) from exc
        if not existing:
            raise
        logger.info("source_tag_created_concurrently", database_name=database_name)
        return existing[0]


def get_or_create_tag(
    storage: SourceTagStorageProtocol,
    database_name: str,
    name: str,
    *,
    color: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Return the tag named ``name`` (by the rule, so in any case), creating it if missing.

    ``color`` and ``description`` are used only when the tag is created.

    Raises:
        TagRuleError: ``name`` breaks the tag rule.

    """
    normalised = validate_tag(name)
    found = storage.get_tags_by_names(database_name, [normalised])
    if found:
        return found[0]
    return _create_tag(storage, database_name, normalised, color=color, description=description)


def get_or_create_tags(
    storage: SourceTagStorageProtocol, database_name: str, names: Sequence[str]
) -> list[dict[str, Any]]:
    """Return the tags named ``names`` (validated, de-duplicated, in order), creating the missing.

    Raises:
        TagRuleError: a name breaks the rule, or there are more than the cap.

    """
    wanted = validate_tags(names)
    by_name = {t["name"]: t for t in storage.get_tags_by_names(database_name, wanted)}
    for name in wanted:
        if name not in by_name:
            by_name[name] = _create_tag(storage, database_name, name)
    return [by_name[name] for name in wanted]


def find_tags(
    storage: SourceTagStorageProtocol, database_name: str, names: Sequence[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Look up the tags named ``names`` without creating any.

    Returns:
        ``(found, unknown)``: the tags found, in the order first named, and
        each name with no tag, as given. A name the rule refuses can name no
        tag, so it is unknown too; this never raises for one.

    Raises:
        TagRuleError: ``names`` is not a list of tags (a bare string, say).

    """
    given: list[tuple[str, str | None]] = []
    for raw in check_tag_list(names):
        try:
            given.append((str(raw), validate_tag(raw)))
        except TagRuleError:
            given.append((str(raw), None))
    wanted = list(dict.fromkeys(name for _raw, name in given if name is not None))
    by_name = {t["name"]: t for t in storage.get_tags_by_names(database_name, wanted)}
    found = [by_name[name] for name in wanted if name in by_name]
    unknown = [raw for raw, name in given if name not in by_name]
    return found, unknown


def source_tag_names(storage: SourceTagStorageProtocol, source_id: str) -> list[str]:
    """Return the names of the source's tags, sorted."""
    return sorted(t["name"] for t in storage.get_source_tags(source_id))


def require_taggable_source(
    storage: SourceAndTagStorage, database_name: str, source_id: str
) -> None:
    """Refuse a source id that names no document, or names a memory.

    Raises:
        NotFoundError: no source in ``database_name`` has the id.
        ValidationError: the source is a memory.

    """
    source = storage.get_source(source_id, database_name)
    if source is None:
        raise NotFoundError("Document", source_id, message=f'No document has the id "{source_id}".')
    if is_memory_source(source):
        msg = f'"{source_id}" is a memory; memories carry no tags.'
        raise ValidationError(msg, field="source_id")


def edit_source_tags(
    storage: SourceAndTagStorage,
    database_name: str,
    source_id: str,
    *,
    add: Sequence[str] = (),
    remove: Sequence[str] = (),
    replace: Sequence[str] | None = None,
) -> list[str]:
    """Add, remove or replace a document's tags (the ``tag_document`` operation).

    ``add`` adds, ``remove`` takes away (a tag the document lacks is a no-op),
    ``replace`` makes the list the whole set (``[]`` clears it). Every name,
    ``remove``'s included, goes through the tag rule: a name the rule refuses
    is refused even in ``remove`` (no document can carry it, so it is most
    likely a typo), rather than ignored.

    The whole edit (reading the current tags, the cap check on the result and
    every write) is one ``storage.write_locked()`` transaction, holding the
    writer lock from its first read: the cap applies to the result, another
    writer's edit waits and then reads this one's, and an edit that is
    refused or fails part-way changes nothing.

    Returns:
        The document's tags after the edit, sorted.

    Raises:
        TagRuleError: ``replace`` with ``add`` or ``remove``, a tag breaks the
            rule, or the result would be over the cap.
        NotFoundError: no document has ``source_id``.
        ValidationError: ``source_id`` is a memory.
        ConflictError: another writer created one of the new tags while this
            edit ran (nothing is changed; trying again succeeds).

    """
    check_tag_edit(add=add, remove=remove, replace=replace)
    if replace is not None:
        target = validate_tags(replace)
        with storage.write_locked():
            require_taggable_source(storage, database_name, source_id)
            tag_ids = [t["id"] for t in get_or_create_tags(storage, database_name, target)]
            storage.set_source_tags(source_id, tag_ids, database_name)
        return source_tag_names(storage, source_id)

    adding = list(dict.fromkeys(validate_tag(t) for t in check_tag_list(add)))
    removing = {validate_tag(t) for t in check_tag_list(remove)}
    with storage.write_locked():
        require_taggable_source(storage, database_name, source_id)
        current = source_tag_names(storage, source_id)
        result = [n for n in dict.fromkeys([*current, *adding]) if n not in removing]
        ensure_tag_count(len(result))
        added = [n for n in adding if n not in removing and n not in current]
        if added:
            tag_ids = [t["id"] for t in get_or_create_tags(storage, database_name, added)]
            storage.add_source_tags(source_id, tag_ids, database_name)
        dropped = [n for n in current if n in removing]
        if dropped:
            found, _unknown = find_tags(storage, database_name, dropped)
            storage.remove_source_tags(source_id, [t["id"] for t in found])
    return source_tag_names(storage, source_id)
