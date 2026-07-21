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
    # Drop only genuinely-blank lines at the edges (e.g. an empty block, or the
    # trailing "" from the final newline) — NOT `.strip()` on the whole blob,
    # which would also eat a trailing space off the last field's value (e.g.
    # `entity: \n` losing its space and becoming key `entity:` with no value).
    lines = fm.split("\n")
    while lines and lines[0].strip() == "":
        lines.pop(0)
    while lines and lines[-1].strip() == "":
        lines.pop()
    for line in lines:
        key, _, value = line.partition(": ")
        meta[key] = value
    return meta, body
