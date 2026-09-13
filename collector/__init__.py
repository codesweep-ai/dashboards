"""Collect what every project depends on, and how far behind it is.

The dependencies page reads one static file, deps.json, and this package writes
it. It reads each project's default branch over plain git and each dependency's
upstream from public registries, so it needs no API key. SPEC.md describes the
file it writes.
"""
