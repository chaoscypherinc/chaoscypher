# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Domain Analyzer Registry with Auto-Discovery.

Auto-discovers domain analyzers from:
1. plugins/ directory - Built-in domains (shared across all databases)
2. Per-database domains/ directory - Custom domains for each database

Domains are configured via JSON-LD files (domain.jsonld or domain.json).
No Python code is required for custom domains.

This registry extends the shared plugin infrastructure while maintaining
full backward compatibility with the existing domain discovery pattern.

Example:
    from chaoscypher_core.services.sources.engine.extraction.domains import (
        get_domain_registry,
    )

    registry = get_domain_registry(settings, database_name="research")
    domain, confidence = registry.get_best_domain(text, filename, metadata)
    guidance = domain.get_guidance()

    # Or force a specific domain
    domain = registry.get_domain_by_name("technical")
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.plugins import BaseRegistry, PluginMetadata
from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
    ConfigurableDomain,
)
from chaoscypher_core.services.sources.engine.extraction.domains.fingerprint import (
    DomainFingerprint,
    compute_domain_content_hash,
)
from chaoscypher_core.services.sources.engine.extraction.utils.template_formatter import (
    format_domain_edge_templates,
    format_domain_examples,
    format_domain_node_templates,
)


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.engine.extraction.domains.base import (
        DomainAnalyzer,
    )
    from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
        ExclusionRule,
    )
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


class DomainRegistry(BaseRegistry["DomainAnalyzer"]):
    """Registry for domain analyzers with auto-discovery.

    Extends BaseRegistry to provide standardized plugin management while
    maintaining backward compatibility with the existing domain discovery pattern.

    Scans two locations for domain folders:
    1. plugins/ - Built-in domains (shared across all databases)
    2. data/databases/{db_name}/domains/ - Per-database custom domains

    Domains are configured via domain.jsonld or domain.json files.

    Attributes:
        settings: Application settings.
        database_name: Current database name for per-database domains.
        _configs: Cached domain configurations.
    """

    def __init__(
        self, settings: EngineSettings | None = None, database_name: str = "default"
    ) -> None:
        """Initialize domain registry with auto-discovery.

        Args:
            settings: Application settings (optional).
            database_name: Database name for per-database domain lookup.
        """
        # Domain-specific state
        self._configs: dict[str, dict[str, Any]] = {}  # name → config
        self._domain_info: dict[str, dict[str, Any]] = {}  # name → cached info for UI

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

        # Build search paths
        search_paths: list[tuple[str, Path]] = [
            ("builtin", domains_dir / "plugins"),
        ]

        # Add user plugins path if settings available
        user_plugins_path = self._get_user_plugins_path()
        if user_plugins_path:
            search_paths.append(("user", user_plugins_path))

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

            audit_log_user_plugin_file(config_path, registry="DomainRegistry")

        # Load JSON config
        content = config_path.read_text(encoding="utf-8")
        try:
            config = json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning(
                "domain_schema_invalid",
                file=str(config_path),
                path_type=path_type,
                error="json_decode",
                error_message=str(exc),
            )
            return

        # Schema-validate the config shape before handing to ConfigurableDomain.
        from pydantic import ValidationError

        from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
            DomainConfigModel,
        )

        try:
            DomainConfigModel.model_validate(config)
        except ValidationError as exc:
            logger.warning(
                "domain_schema_invalid",
                file=str(config_path),
                path_type=path_type,
                error="schema_validation",
                error_message=str(exc),
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

    # Detection-confidence guardrail (set from the 2026-05-23 38-fixture audit):
    # every correctly-detected fixture scored >= 1.02 on its right domain,
    # so 1.0 is the highest absolute floor we can safely apply without losing
    # any of the known-good detections (0.02 headroom). At 1.0 the floor
    # catches 7 of 17 mismatches whose wrong winners scored < 1.0 and routes
    # them to `generic` — better to use the broadest schema than to lock in
    # a wrong-domain template set. The remaining 10 mismatches have wrong
    # winners >= 1.0 (over-eager broad-vocab domains like `news`, `historical`,
    # `political`); those need targeted keyword pruning in their JSON-LDs.
    #
    # A runner-up-gap rule was tried at 0.10 / 0.20 but killed 3-6
    # narrow-margin correct detections (biographical, historical) for only
    # marginal mismatch reduction, so we keep it disabled.
    _DETECTION_ABSOLUTE_MIN: float = 1.0

    def get_best_domain(
        self,
        text: str,
        filename: str,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[Any, float]:
        """Run all analyzers and return the best match.

        Args:
            text: Sample text from document.
            filename: Original filename.
            metadata: Optional document metadata.

        Returns:
            Tuple of (domain_instance, confidence).
            Returns generic domain when no domain crosses the absolute floor
            or when the top candidate is ambiguously close to the runner-up.
        """
        # Collect every can_handle=True candidate (generic excluded, sorted
        # high-to-low) so we can apply the runner-up gap / absolute-floor rule
        # below. rank_domains is the single source of this list.
        candidates = self.rank_domains(text, filename, metadata)

        fallback_reason: str | None = None
        best_domain: Any = None
        best_confidence: float = 0.0
        if not candidates:
            fallback_reason = "no_candidates_above_per_plugin_threshold"
        else:
            winner, winner_conf = candidates[0]
            if winner_conf < self._DETECTION_ABSOLUTE_MIN:
                fallback_reason = "winner_below_absolute_min"
            else:
                best_domain = winner
                best_confidence = winner_conf

        if fallback_reason is not None:
            best_domain = self._plugins.get("generic")
            best_confidence = 0.1
            logger.debug(
                "domain_detection_fell_back_to_generic",
                reason=fallback_reason,
                top_candidates=[(d.name, round(c, 3)) for d, c in candidates[:3]],
                absolute_min=self._DETECTION_ABSOLUTE_MIN,
            )

        if best_domain is None:
            logger.error("no_domains_available", message="No domains registered")
            # Return a minimal fallback
            return _MinimalFallbackDomain(), 0.0

        logger.debug(
            "domain_selected",
            domain=best_domain.name,
            confidence=best_confidence,
        )

        return best_domain, best_confidence

    def rank_domains(
        self,
        text: str,
        filename: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[tuple[Any, float]]:
        """Return all can_handle candidate domains, highest score first.

        This is the candidate list that :meth:`get_best_domain` builds and then
        reduces to a single winner. ``generic`` is excluded from the competition
        (it is only the fallback), plugins reporting ``can_handle=False`` or
        raising are skipped, and the list is sorted by descending confidence.

        No absolute floor and no generic fallback are applied here — that
        policy lives in :meth:`get_best_domain` and ``detect_extraction_domain``.
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
            if name == "generic":
                continue
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
        the per-domain info computed once at registration time. Returns
        ``None`` when no domain of this name is registered (so callers can
        treat "plugin gone" as not-stale).
        """
        info = self._domain_info.get(name)
        if info is None:
            return None
        return DomainFingerprint(
            version=self._configs.get(name, {}).get("version"),
            content_hash=info["content_hash"],
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

        node_fmt = format_domain_node_templates(templates)
        edge_fmt = format_domain_edge_templates(templates)
        examples_fmt = format_domain_examples(examples)

        total_chars = len(guidance) + len(node_fmt) + len(edge_fmt) + len(examples_fmt)
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

    def get_type_compatibility(self) -> dict[str, list[str]]:
        """Get type compatibility groups."""
        return {}

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
