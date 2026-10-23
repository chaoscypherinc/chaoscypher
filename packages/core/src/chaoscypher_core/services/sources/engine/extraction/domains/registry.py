# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Domain Analyzer Registry with Auto-Discovery.

Auto-discovers domain analyzers from:
1. plugins/ directory - Built-in domains (shared across all databases)
2. {data_dir}/plugins/domains/ - user packs, shared by every database

Domains are configured via JSON-LD files (domain.jsonld or domain.json).
No Python code is required for custom domains.

This registry extends the shared plugin infrastructure while maintaining
full backward compatibility with the existing domain discovery pattern.

Example:
    from chaoscypher_core.services.sources.engine.extraction.domains import (
        get_domain_registry,
    )

    registry = get_domain_registry(settings, database_name="research")
    proposal = registry.propose_packs(text, filename, metadata)
    proposal.packs  # ("historical", "literary"): every source also has the floor

    # Or force a specific domain
    domain = registry.get_domain_by_name("technical")
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, NamedTuple

import structlog

from chaoscypher_core.plugins import BaseRegistry, PluginMetadata
from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
    ConfigurableDomain,
    detection_base_score,
    detection_max_confidence,
)
from chaoscypher_core.services.sources.engine.extraction.domains.core_types import FLOOR_NAME
from chaoscypher_core.services.sources.engine.extraction.domains.fingerprint import (
    DomainFingerprint,
    compute_domain_content_hash,
    stack_content_hash,
)
from chaoscypher_core.services.sources.engine.extraction.domains.packs import (
    MAX_PROPOSED_PACKS,
    PACKS_SEPARATOR,
    stored_name_packs,
)
from chaoscypher_core.services.sources.engine.extraction.utils.template_formatter import (
    format_domain_edge_templates,
    format_domain_examples,
    format_domain_node_templates,
)


if TYPE_CHECKING:
    from collections.abc import Sequence

    from chaoscypher_core.services.sources.engine.extraction.domains.base import (
        DomainAnalyzer,
    )
    from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
        ExclusionRule,
    )
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


FLOOR_ONLY_CONFIDENCE: Final[float] = 0.1
"""The confidence detection reports when it proposes no pack (the floor alone)."""


class RankedDomain(NamedTuple):
    """One detection candidate: a pack and its raw score (uncapped)."""

    domain: Any
    score: float


@dataclass(frozen=True)
class PackProposal:
    """What detection proposes for a source (:meth:`DomainRegistry.propose_packs`).

    Attributes:
        packs: The proposed packs, highest score first, at most
            :data:`MAX_PROPOSED_PACKS`; ``()`` is the floor alone.
        ranking: Every can-handle candidate, highest raw score first.
        low_confidence: True when no pack cleared the evidence floor.
        reason: ``no_pack_above_floor``, ``one_pack_above_floor``,
            ``packs_within_margin`` (two packs, the lead under the margin)
            or ``packs_above_floor``.
    """

    packs: tuple[str, ...]
    ranking: list[RankedDomain]
    low_confidence: bool
    reason: str


class DomainRegistry(BaseRegistry["DomainAnalyzer"]):
    """Registry for domain analyzers with auto-discovery.

    Extends BaseRegistry to provide standardized plugin management while
    maintaining backward compatibility with the existing domain discovery pattern.

    Scans two locations for domain folders:
    1. plugins/ - Built-in domains (shared across all databases)
    2. {data_dir}/plugins/domains/ - user packs (one directory, every database)

    Domains are configured via domain.jsonld or domain.json files.

    Attributes:
        settings: Application settings.
        database_name: Current database name for per-database domains.
        _configs: Cached domain configurations.
    """

    def __init__(
        self,
        settings: EngineSettings | None = None,
        database_name: str = "default",
        user_plugin_dirs: Sequence[Path] | None = None,
    ) -> None:
        """Initialize domain registry with auto-discovery.

        Args:
            settings: Application settings (optional).
            database_name: Database name for per-database domain lookup.
            user_plugin_dirs: Further directories of user domain configs, scanned
                after the settings' ``data/plugins/domains/`` (tests, tools).
        """
        # Domain-specific state
        self._configs: dict[str, dict[str, Any]] = {}  # name → config
        self._domain_info: dict[str, dict[str, Any]] = {}  # name → cached info for UI
        self._load_errors: dict[str, str] = {}  # name → why its config was refused
        self._user_plugin_dirs: tuple[Path, ...] = tuple(Path(p) for p in user_plugin_dirs or ())

        # Call parent init (triggers _discover)
        super().__init__(settings=settings, database_name=database_name)

    def _discover(self) -> None:
        """Auto-discover domains from built-in and user plugin directories.

        Scans two locations:
        1. plugins/ - Built-in domains (shipped with package)
        2. data/plugins/domains/ - User custom domains

        Registers domains by loading *.jsonld or *.json config files.

        Implements BaseRegistry._discover().
        """
        domains_dir = Path(__file__).parent
        self._load_errors.clear()

        # Build search paths
        search_paths: list[tuple[str, Path]] = [
            ("builtin", self.builtin_plugin_dir()),
        ]

        # Add user plugins path if settings available
        user_plugins_path = self._get_user_plugins_path()
        if user_plugins_path:
            search_paths.append(("user", user_plugins_path))
        search_paths.extend(("user", d) for d in self._user_plugin_dirs)

        logger.info(
            "domain_discovery_started",
            domains_directory=str(domains_dir),
            database_name=self.database_name,
        )

        for path_type, search_dir in search_paths:
            if not search_dir.exists():
                logger.debug(
                    "domain_search_path_missing",
                    path_type=path_type,
                    path=str(search_dir),
                )
                continue

            self._scan_directory(search_dir, path_type)

        logger.info(
            "domain_discovery_complete",
            domains_found=list(self._plugins.keys()),
            total_count=len(self._plugins),
        )

    @classmethod
    def builtin_plugin_dir(cls) -> Path:
        """The directory of the shipped domain configs (the floor and the packs)."""
        return Path(__file__).parent / "plugins"

    def names(self) -> list[str]:
        """The names of every registered domain, sorted."""
        return sorted(self._plugins)

    def load_errors(self) -> dict[str, str]:
        """Why each refused domain config was refused, by its name (the file stem when unnamed)."""
        return dict(self._load_errors)

    def _get_user_plugins_path(self) -> Path | None:
        """Get user plugins domains path from settings.

        Returns:
            Path to data/plugins/domains/ directory, or None.
        """
        if self.settings is None:
            return None

        # Try to get data_dir from settings
        data_dir = getattr(self.settings, "data_dir", None)
        if data_dir is None:
            # Try paths.data_dir pattern
            paths = getattr(self.settings, "paths", None)
            if paths:
                data_dir = getattr(paths, "data_dir", None)

        if data_dir is None:
            return None

        return Path(data_dir) / "plugins" / "domains"

    def _scan_directory(self, search_dir: Path, path_type: str) -> None:
        """Scan a directory for domain config files.

        Supports two patterns:
        1. Single *.jsonld files (e.g., technical.jsonld) - preferred
        2. Folders with domain.jsonld inside (e.g., technical/domain.jsonld) - legacy

        Args:
            search_dir: Directory to scan.
            path_type: Type identifier (builtin or user).
        """
        if not search_dir.exists():
            return

        for item in sorted(search_dir.iterdir(), key=lambda p: p.name):
            # Pattern 1: Single .jsonld file (preferred)
            if item.is_file() and item.suffix in [".jsonld", ".json"]:
                try:
                    self._register_domain_from_config(item.parent, item, path_type)
                except Exception as e:
                    logger.warning(
                        "domain_registration_failed",
                        file=item.name,
                        error=str(e),
                        exc_info=True,
                    )
                continue

            # Pattern 2: Folder with domain.jsonld inside (legacy)
            if item.is_dir() and not item.name.startswith("_"):
                config_path = item / "domain.jsonld"
                if not config_path.exists():
                    config_path = item / "domain.json"

                if config_path.exists():
                    try:
                        self._register_domain_from_config(item, config_path, path_type)
                    except Exception as e:
                        logger.warning(
                            "domain_registration_failed",
                            folder=item.name,
                            error=str(e),
                            exc_info=True,
                        )

    def _register_domain_from_config(
        self,
        _domain_folder: Path,
        config_path: Path,
        path_type: str,
    ) -> None:
        """Register a domain from its JSON-LD config file.

        Args:
            _domain_folder: Path to domain folder (unused, kept for signature consistency).
            config_path: Path to domain.jsonld or domain.json file.
            path_type: Type identifier (builtin or user).
        """
        if path_type == "user":
            from chaoscypher_core.plugins.user_plugin_loader import (
                audit_log_user_plugin_file,
            )

            if not audit_log_user_plugin_file(config_path, registry="DomainRegistry"):
                return

        # Load JSON config
        content = config_path.read_text(encoding="utf-8")
        try:
            config = json.loads(content)
        except json.JSONDecodeError as exc:
            self._load_errors[config_path.stem] = f"not JSON: {exc}"
            logger.warning(
                "domain_schema_invalid",
                file=str(config_path),
                path_type=path_type,
                error="json_decode",
                error_message=str(exc),
            )
            return
        name = str(config.get("name") or config_path.stem) if isinstance(config, dict) else ""
        name = name or config_path.stem

        # Schema-validate the config shape before handing to ConfigurableDomain.
        from pydantic import ValidationError

        from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
            DomainConfigModel,
            describe_validation_error,
        )

        try:
            DomainConfigModel.model_validate(config)
        except ValidationError as exc:
            self._load_errors[name] = describe_validation_error(exc)
            logger.warning(
                "domain_schema_invalid",
                file=str(config_path),
                path_type=path_type,
                error="schema_validation",
                error_message=self._load_errors[name],
            )
            return
        floor_named = name.strip().lower() == FLOOR_NAME
        if path_type != "builtin" and (config.get("role") == "floor" or floor_named):
            # One floor: the shipped generic plugin. A user config only adds (a pack),
            # under a name of its own: it never replaces the floor.
            self._load_errors[name] = (
                f"user domain {name!r} refused: only the built-in 'generic' is the floor, "
                "and a user domain is a pack with a name of its own"
            )
            logger.warning(
                "domain_schema_invalid",
                file=str(config_path),
                path_type=path_type,
                error="user_floor",
                error_message=self._load_errors[name],
            )
            return

        # Create ConfigurableDomain instance
        instance = ConfigurableDomain(config, self.settings)

        # Register using parent's _register_by_id
        self._register_by_id(
            instance.name,
            instance,
            source_path=config_path,
            is_user=(path_type == "user"),
        )
        self._configs[instance.name] = config

        # Cache domain info for UI (calculated once at load time)
        self._domain_info[instance.name] = {
            "name": instance.name,
            "description": instance.description,
            "icon": config.get("icon"),
            "version": config.get("version", "unknown"),
            "content_hash": compute_domain_content_hash(instance),
            "builtin": config.get("builtin", False),
            "has_examples": bool(instance.get_examples()),
            "prompt_tokens": self._estimate_prompt_tokens(instance),
            "extraction_density": instance.get_extraction_density(),
            # ``floor`` for the floor every source has, ``pack`` for one a
            # source may add (the UI offers packs; the floor is implied).
            "role": getattr(instance, "role", None) or "pack",
            # A pack's subtypes as the UI shows them: the stored ``entity_subtype``
            # value, the pack's name for it, its core type, and its own icon/colour
            # (a node is drawn with its core type's template; a subtype may show its own icon).
            "subtypes": [
                {"key": s.key, "name": s.name, "of": s.of, "icon": s.icon, "color": s.color}
                for s in instance.subtypes()
            ],
        }

        logger.debug(
            "domain_registered",
            domain=instance.name,
            version=config.get("version", "unknown"),
            builtin=config.get("builtin", False),
            path_type=path_type,
            prompt_tokens=self._domain_info[instance.name]["prompt_tokens"],
        )

    def _get_plugin_id(self, plugin: DomainAnalyzer) -> str:
        """Extract plugin ID from a domain instance.

        Args:
            plugin: Domain instance.

        Returns:
            Domain name.
        """
        return plugin.name

    def _get_plugin_metadata(self, plugin: DomainAnalyzer) -> PluginMetadata:
        """Extract metadata from a domain instance.

        Falls back to generating metadata from name/description if domain
        doesn't implement the metadata property.

        Args:
            plugin: Domain instance.

        Returns:
            PluginMetadata for the domain.
        """
        # Try to get metadata from plugin
        if hasattr(plugin, "metadata"):
            try:
                return plugin.metadata
            except (AttributeError, NotImplementedError):  # fmt: skip
                pass

        # Generate metadata from domain info
        config = self._configs.get(plugin.name, {})
        return PluginMetadata(
            plugin_id=plugin.name,
            name=plugin.name.title(),
            description=plugin.description,
            version=config.get("version", "1.0.0"),
            author=config.get("author", ""),
            category="domain",
            builtin=config.get("builtin", False),
        )

    # --- Backward-compatible API ---

    # Auto-detection rule (2026-10-06, audit W2-5). A plugin's score is its
    # ``base_score`` plus ``per_keyword_boost * weight`` for each distinct
    # keyword present, plus extension/doc-type/pattern boosts, uncapped
    # (configurable.py); the confidence reported is capped at the winner's
    # ``max_confidence`` (``detection_max_confidence``). Raw scores rank the candidates, but a raw floor is not
    # on any plugin's scale: the old ``_DETECTION_ABSOLUTE_MIN = 1.0`` sat
    # above what the product's ~5-12k-char sample reaches (Book One: literary
    # 0.74, the runner-up 0.46), so every auto-detect fell back to generic.
    # The floor is now on the winner's own scale, its *evidence* (score minus
    # its base score). Since packs (2026-10-08, spec § 5) detection proposes
    # every pack over the floor, at most two: a near-tie proposes both, the
    # higher first, instead of falling back to the floor alone.
    _DETECTION_MIN_EVIDENCE: float = 0.30
    """Least evidence (score above a pack's own base) a proposed pack needs: ~8 hits at the usual 0.04 boost.

    Book One's literary winner has +0.54; README-sized technical docs +0.35 to
    +1.0; a short license or roadmap page +0.18 to +0.22 (falls to generic).
    """

    _DETECTION_MIN_MARGIN: float = 0.08
    """The raw-score lead under which two proposed packs are a near-tie (reason ``packs_within_margin``).

    Two keyword hits at the usual 0.04 boost. Before packs a lead under it
    fell back to generic; now it only names the order.

    The 2026-05-23 audit tried 0.10 / 0.20 and lost narrow correct
    detections (biographical, historical); 0.08 keeps Book One (lead 0.28 on
    the product sample) and sends a terms-of-service page where news leads
    legal by 0.04 to generic.
    """

    def _detection_evidence(self, domain: Any, confidence: float) -> float:
        """``confidence`` minus ``domain``'s own base score (what its keyword hits added)."""
        return confidence - detection_base_score(self._configs.get(domain.name, {}))

    def propose_packs(
        self,
        text: str,
        filename: str,
        metadata: dict[str, Any] | None = None,
    ) -> PackProposal:
        """Propose the packs a source should extract with, on the floor every source has.

        Detection is a hint (spec 2026-10-07 § 5): every pack whose evidence
        (score minus its own base score, :meth:`_detection_evidence`) is at
        least :attr:`_DETECTION_MIN_EVIDENCE` is proposed, ordered by score
        (:meth:`rank_domains`), at most :data:`MAX_PROPOSED_PACKS`. The
        runner-up margin (:attr:`_DETECTION_MIN_MARGIN`) only names the
        order (``reason``): two packs within it are both proposed, the higher
        first, and it never empties the proposal. No pack above the floor is
        the floor alone, ``low_confidence`` and reason ``no_pack_above_floor``.

        Args:
            text: Sample text from document.
            filename: Original filename.
            metadata: Optional document metadata.

        Returns:
            The :class:`PackProposal`; its ``ranking`` is every can-handle
            candidate, highest raw score first.
        """
        ranking = [
            RankedDomain(d, score) for d, score in self.rank_domains(text, filename, metadata)
        ]
        # Rounded: scores are sums of 0.04-style boosts, and float error must not
        # decide a score that sits exactly on the floor.
        above = [
            r
            for r in ranking
            if round(self._detection_evidence(r.domain, r.score), 6) >= self._DETECTION_MIN_EVIDENCE
        ][:MAX_PROPOSED_PACKS]
        packs = tuple(str(r.domain.name) for r in above)
        if not above:
            reason = "no_pack_above_floor"
        elif len(above) == 1:
            reason = "one_pack_above_floor"
        elif round(above[0].score - above[1].score, 6) < self._DETECTION_MIN_MARGIN:
            reason = "packs_within_margin"
        else:
            reason = "packs_above_floor"
        logger.debug(
            "domain_packs_proposed",
            packs=list(packs),
            reason=reason,
            top_candidates=[(r.domain.name, round(r.score, 3)) for r in ranking[:3]],
            min_evidence=self._DETECTION_MIN_EVIDENCE,
            min_margin=self._DETECTION_MIN_MARGIN,
        )
        return PackProposal(packs=packs, ranking=ranking, low_confidence=not packs, reason=reason)

    def proposal_confidence(self, proposal: PackProposal) -> float:
        """The confidence a proposal reports: its first pack's raw score capped at that pack's ``max_confidence``.

        Ranked and judged on raw scores, reported capped (a whole novel scored
        literary 1.62). The floor alone reports
        :data:`FLOOR_ONLY_CONFIDENCE`.
        """
        first = self.first_proposed(proposal)
        if first is None:
            return FLOOR_ONLY_CONFIDENCE
        return min(first.score, detection_max_confidence(self._configs.get(first.domain.name, {})))

    @staticmethod
    def first_proposed(proposal: PackProposal) -> RankedDomain | None:
        """The ranking entry of the proposal's first pack, or None for the floor alone.

        Not ``ranking[0]``: the top raw score can fall short of its own
        evidence floor (a high base score) while the runner-up clears it, and
        then the first proposed pack is the runner-up.
        """
        if not proposal.packs:
            return None
        return next((r for r in proposal.ranking if r.domain.name == proposal.packs[0]), None)

    def get_best_domain(
        self,
        text: str,
        filename: str,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[Any, float]:
        """The first proposed pack and its confidence, or the floor at 0.1.

        .. deprecated::
            A thin wrapper over :meth:`propose_packs` kept for one release:
            a source extracts with every proposed pack, so a caller that
            reads one domain misses the second. Call :meth:`propose_packs`.

        Args:
            text: Sample text from document.
            filename: Original filename.
            metadata: Optional document metadata.

        Returns:
            Tuple of (domain_instance, confidence): ``packs[0]`` at
            :meth:`proposal_confidence`, else the floor at
            :data:`FLOOR_ONLY_CONFIDENCE` (a minimal stand-in at 0.0 when no
            floor is registered).
        """
        proposal = self.propose_packs(text, filename, metadata)
        first = self.first_proposed(proposal)
        if first is not None:
            return first.domain, self.proposal_confidence(proposal)
        floor = self._plugins.get(FLOOR_NAME)
        if floor is None:
            logger.error("no_domains_available", message="No domains registered")
            return _MinimalFallbackDomain(), 0.0
        return floor, FLOOR_ONLY_CONFIDENCE

    def rank_domains(
        self,
        text: str,
        filename: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[tuple[Any, float]]:
        """Return all can_handle candidate domains, highest score first.

        This is the candidate list :meth:`propose_packs` reads. The floor is
        excluded from the competition (every source has it), plugins
        reporting ``can_handle=False`` or raising are skipped, and the list
        is sorted by descending confidence.

        No evidence floor or proposal limit is applied here — that policy
        lives in :meth:`propose_packs`.
        The list may be empty when no domain can handle the content.

        Args:
            text: Sample text from document.
            filename: Original filename.
            metadata: Optional document metadata.

        Returns:
            List of ``(domain_instance, confidence)`` tuples, sorted by
            descending confidence.
        """
        if metadata is None:
            metadata = {}

        candidates: list[tuple[Any, float]] = []
        for name, domain in self._plugins.items():
            if name == FLOOR_NAME or getattr(domain, "role", None) == "floor":
                continue  # the floor applies to every source; detection picks packs
            try:
                can_handle, confidence = domain.can_analyze(text, filename, metadata)
                if can_handle:
                    candidates.append((domain, confidence))
            except Exception as e:
                logger.warning(
                    "domain_analysis_failed",
                    domain=name,
                    error=str(e),
                )

        candidates.sort(key=lambda x: -x[1])
        return candidates

    def get_domain(self, name: str) -> DomainAnalyzer | None:
        """Get a specific domain by name.

        Args:
            name: Domain identifier.

        Returns:
            Domain instance or None if not found.
        """
        return self.get(name)

    def get_domain_fingerprint(self, name: str) -> DomainFingerprint | None:
        """Return the ``(version, content_hash)`` fingerprint for a domain.

        Reads the version from the loaded config and the content hash from
        the per-domain info computed once at registration time. A label of
        several packs (``historical+literary``) gives their versions joined
        with ``+`` and :func:`stack_content_hash` over their hashes. Returns
        ``None`` when no domain of this name (or a member of the label) is
        registered (so callers can treat "plugin gone" as not-stale).
        """
        info = self._domain_info.get(name)
        if info is not None:
            return DomainFingerprint(
                version=self._configs.get(name, {}).get("version"),
                content_hash=info["content_hash"],
            )
        # The packs' label (``historical+literary``): the members' versions and
        # one hash over their hashes (``stack_content_hash``).
        packs = sorted(set(stored_name_packs(name)))
        if len(packs) < 2 or any(p not in self._domain_info for p in packs):
            return None
        content_hash = stack_content_hash(
            {p: self._domain_info[p]["content_hash"] for p in packs}, packs
        )
        if content_hash is None:  # pragma: no cover - every member is known above
            return None
        return DomainFingerprint(
            version=PACKS_SEPARATOR.join(
                str(self._configs.get(p, {}).get("version")) for p in packs
            ),
            content_hash=content_hash,
        )

    def _estimate_prompt_tokens(self, domain: DomainAnalyzer) -> int:
        """Estimate tokens used by this domain's prompt components.

        Uses 4 chars ≈ 1 token approximation for quick estimation.

        Args:
            domain: Domain instance to estimate.

        Returns:
            Estimated token count for domain's prompt components.
        """
        guidance = domain.get_guidance() or ""
        templates = domain.get_templates()
        examples = domain.get_examples()

        # A pack has no node templates (the floor's core types are not its own): its
        # subtype names are what it adds to the node lines.
        node_fmt = format_domain_node_templates(templates) if templates["node_templates"] else ""
        subtypes = getattr(domain, "subtypes", None)
        subtype_chars = sum(len(s.name) + 2 for s in subtypes()) if callable(subtypes) else 0
        edge_fmt = format_domain_edge_templates(templates)
        examples_fmt = format_domain_examples(examples)

        total_chars = (
            len(guidance) + len(node_fmt) + subtype_chars + len(edge_fmt) + len(examples_fmt)
        )
        return total_chars // 4  # 4 chars ≈ 1 token

    def list_domain_info(self) -> list[dict[str, Any]]:
        """List all domain info for UI (e.g., Add Source dialog).

        Returns cached info including prompt token estimates.

        Returns:
            List of domain info dictionaries.
        """
        return list(self._domain_info.values())


class _MinimalFallbackDomain:
    """Minimal fallback domain when no domains are registered."""

    @property
    def name(self) -> str:
        """Domain name."""
        return "fallback"

    @property
    def description(self) -> str:
        """Domain description."""
        return "Minimal fallback domain"

    @property
    def metadata(self) -> PluginMetadata:
        """Domain metadata."""
        return PluginMetadata(
            plugin_id="fallback",
            name="Fallback",
            description="Minimal fallback domain",
            category="domain",
        )

    def can_analyze(
        self,
        text: str,
        filename: str,
        metadata: dict[str, Any],
    ) -> tuple[bool, float]:
        """Check if domain can analyze content."""
        return True, 0.0

    def get_guidance(self) -> str:
        """Get extraction guidance."""
        return ""

    def get_templates(self) -> dict[str, list[dict[str, Any]]]:
        """Get domain templates."""
        return {"node_templates": [], "edge_templates": []}

    def get_normalization_rules(self) -> dict[str, list[str]]:
        """Get normalization rules."""
        return {}

    def get_examples(self) -> dict[str, list[dict[str, Any]]]:
        """Get domain-specific extraction examples."""
        return {}

    def get_extraction_density(self) -> float:
        """Get extraction density factor."""
        return 1.0

    def get_entity_exclusions(self) -> list[ExclusionRule]:
        """Get domain-specific entity exclusion rules."""
        return []

    def get_title_words(self) -> list[str]:
        """Get title/honorific words to filter."""
        return []

    def get_feminine_titles(self) -> list[str]:
        """No feminine titles of its own (the name rule's universal ones apply)."""
        return []

    def get_masculine_titles(self) -> list[str]:
        """No masculine titles of its own."""
        return []

    def get_generation_words(self) -> dict[str, str]:
        """No generation words of its own."""
        return {}

    def get_name_forms(self) -> dict[str, list[dict[str, Any]]]:
        """No name forms."""
        return {}

    def get_relation_vocabulary(self) -> dict[str, dict[str, list[str]]]:
        """No relation vocabulary of its own (the universal one applies)."""
        return {}

    def get_vocabulary_exclusions(self) -> list[str]:
        """No labels taken out of the universal vocabulary."""
        return []

    def get_role_words(self) -> list[str]:
        """No role words of its own."""
        return []

    def get_title_equivalents(self) -> list[list[str]]:
        """No title spellings of its own."""
        return []

    def get_strict_entity_types(self) -> bool:
        """Get strict entity type enforcement."""
        return False

    def get_extraction_limits(self) -> dict[str, float | int]:
        """Get relationship extraction limits."""
        return {}

    def get_filtering_mode(self) -> str | None:
        """Get the domain's filtering-preset selector, if any."""
        return None

    def get_property_type_mapping(self) -> dict[str, dict[str, str]]:
        """Get property-type mapping for type rescue."""
        return {}

    def get_evidence_validation_mode(self) -> str | None:
        """Get evidence validation mode."""
        return None


__all__ = ["DomainRegistry"]
