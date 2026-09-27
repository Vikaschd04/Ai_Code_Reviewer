"""Engine cache keys change whenever any input that can change a per-file result changes."""

from __future__ import annotations

import uuid
from dataclasses import replace

from crp_analysis.engines.base import CacheIdentity
from crp_analysis.engines.opengrep import OpengrepAdapter
from crp_worker.engine_cache import CacheScope

WS, PROJECT = uuid.uuid4(), uuid.uuid4()
BASE = CacheScope(WS, PROJECT, "pmd", "7.17.0", CacheIdentity("a" * 64, "c" * 64), 1_000_000)


def test_identical_inputs_give_identical_keys() -> None:
    assert BASE.key("A.java", "b" * 64) == replace(BASE).key("A.java", "b" * 64)


def test_each_input_invalidates() -> None:
    key = BASE.key("A.java", "b" * 64)
    variants = [
        BASE.key("A.java", "d" * 64),  # content
        BASE.key("B.java", "b" * 64),  # path (rules may depend on it)
        replace(BASE, engine_version="7.18.0").key("A.java", "b" * 64),
        replace(BASE, identity=CacheIdentity("e" * 64, "c" * 64)).key("A.java", "b" * 64),
        replace(BASE, identity=CacheIdentity("a" * 64, "f" * 64)).key("A.java", "b" * 64),
        replace(BASE, max_file_bytes=10).key("A.java", "b" * 64),
        replace(BASE, project_id=uuid.uuid4()).key("A.java", "b" * 64),  # tenant boundary
        replace(BASE, engine="eslint").key("A.java", "b" * 64),
    ]
    assert key not in variants and len(set(variants)) == len(variants)


def test_adapter_configuration_changes_the_config_fingerprint() -> None:
    small = OpengrepAdapter(None, timeout_seconds=1, max_output_bytes=1, max_target_bytes=10)
    large = OpengrepAdapter(None, timeout_seconds=1, max_output_bytes=1, max_target_bytes=20)
    assert small.cache_identity().ruleset_sha256 == large.cache_identity().ruleset_sha256
    assert small.cache_identity().config_fingerprint != large.cache_identity().config_fingerprint
