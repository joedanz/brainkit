"""Shared frontmatter parsing for the flat `key: value` blocks brainkit uses.

This is deliberately NOT a YAML parser. Promotions and drafts carry a small,
fixed set of scalar keys, and the round-trip guarantee that matters is that the
BODY survives byte-for-byte (approve/reject rewrite the frontmatter but copy the
body verbatim). Matching the historical `text.split("---\\n", 2)` +
`line.partition(": ")` behavior exactly keeps that guarantee — pyyaml would
re-quote and reflow. `_meta/*.yaml` config is the only thing parsed as real YAML
(see schemas.py).
"""

from __future__ import annotations


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a document into (frontmatter dict, body).

    A frontmatter block must open on the very first line with ``---`` and have a
    matching closing ``---``. Anything else is treated as body with no
    frontmatter, returning ``({}, text)`` unchanged. Keys are parsed by
    splitting each line on the first ``": "`` (the rest of the line is the value,
    verbatim), matching promotions/doctor's original loops.
    """
    parts = text.split("---\n", 2)
    if len(parts) < 3 or parts[0] != "":
        return {}, text
    _, fm, body = parts
    meta: dict[str, str] = {}
    for line in fm.strip().splitlines():
        key, _, value = line.partition(": ")
        meta[key] = value
    return meta, body
