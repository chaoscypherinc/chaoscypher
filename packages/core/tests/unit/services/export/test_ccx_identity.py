# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for stable CCX IRI minting (pure; no I/O)."""

from chaoscypher_core.services.export import ccx_identity


class TestBaseIri:
    """The BASE_IRI constant anchors every minted IRI."""

    def test_base_iri_constant(self):
        """BASE_IRI is the Chaos Cypher URN namespace prefix."""
        assert ccx_identity.BASE_IRI == "urn:ccx:chaoscypher:"


class TestMintIri:
    """mint_iri produces deterministic, kind-scoped IRIs."""

    def test_mint_shape(self):
        """A minted IRI is BASE_IRI + kind/local_id."""
        assert ccx_identity.mint_iri("node", "abc123") == "urn:ccx:chaoscypher:node/abc123"

    def test_mint_is_stable(self):
        """Minting the same inputs twice yields the same IRI."""
        first = ccx_identity.mint_iri("rel", "edge-1")
        second = ccx_identity.mint_iri("rel", "edge-1")
        assert first == second

    def test_mint_kinds_distinct(self):
        """Different kinds with the same local id mint distinct IRIs."""
        node = ccx_identity.mint_iri("node", "x")
        source = ccx_identity.mint_iri("source", "x")
        assert node != source
        assert node == "urn:ccx:chaoscypher:node/x"
        assert source == "urn:ccx:chaoscypher:source/x"


class TestResolveIri:
    """resolve_iri prefers a persisted foreign IRI, else mints."""

    def test_resolve_prefers_persisted_ccx_iri(self):
        """A persisted ccx_iri wins over a minted one (cross-package merge)."""
        record = {"id": "n1", "ccx_iri": "https://example.org/foreign/thing"}
        assert ccx_identity.resolve_iri("node", record) == "https://example.org/foreign/thing"

    def test_resolve_mints_when_no_ccx_iri(self):
        """With no ccx_iri the IRI is minted from the local id."""
        record = {"id": "n2"}
        assert ccx_identity.resolve_iri("node", record) == "urn:ccx:chaoscypher:node/n2"

    def test_resolve_mints_when_ccx_iri_none(self):
        """An explicit ccx_iri of None falls back to minting."""
        record = {"id": "n3", "ccx_iri": None}
        assert ccx_identity.resolve_iri("source", record) == "urn:ccx:chaoscypher:source/n3"

    def test_resolve_mints_when_ccx_iri_empty(self):
        """An empty-string ccx_iri is falsy and falls back to minting."""
        record = {"id": "n4", "ccx_iri": ""}
        assert ccx_identity.resolve_iri("rel", record) == "urn:ccx:chaoscypher:rel/n4"


class TestLocalIdFromIri:
    """local_id_from_iri reverses minting, only for our own IRIs."""

    def test_roundtrip_minted(self):
        """The local id round-trips through mint_iri / local_id_from_iri."""
        iri = ccx_identity.mint_iri("node", "round-trip-id")
        assert ccx_identity.local_id_from_iri(iri) == "round-trip-id"

    def test_returns_id_for_minted(self):
        """A minted IRI yields its trailing local id."""
        assert ccx_identity.local_id_from_iri("urn:ccx:chaoscypher:template/tmpl-9") == "tmpl-9"

    def test_returns_none_for_foreign(self):
        """A foreign IRI (not in our namespace) yields None."""
        assert ccx_identity.local_id_from_iri("https://example.org/foreign/thing") is None

    def test_roundtrip_local_id_with_slash(self):
        """A local id containing '/' round-trips verbatim (true inverse)."""
        iri = ccx_identity.mint_iri("node", "a/b")
        assert iri == "urn:ccx:chaoscypher:node/a/b"
        assert ccx_identity.local_id_from_iri(iri) == "a/b"

    def test_returns_none_for_kind_only(self):
        """An IRI with only a kind segment (no local id) yields None."""
        assert ccx_identity.local_id_from_iri("urn:ccx:chaoscypher:node") is None
