"""Snapshot-scoped structural graph: per-file relation extraction, manifests and resolution.

Everything here is syntax-level and deterministic. No code is executed, no dependency is
downloaded and nothing is inferred by a model. Every edge carries source evidence and a
classification (resolved / declared / inferred / unresolved) with a reason.
"""
