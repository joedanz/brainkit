"""Heading-aware markdown chunking for the retrieval index.

Splits a note into retrieval units that respect document structure: each chunk
carries the heading path it lives under (``Quarterly Plan > Q3 > Risks``) so that
context travels with the embedding, and code fences are never split. Chunk text
stays clean for FTS/snippets; the heading context is folded in only at embedding
time via `embedding_input`.

Chunking is pure and deterministic — same bytes in, same chunks out — which is
what lets the indexer trust a content sha to mean "nothing to re-embed".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from brain.compiler import WIKILINK_RE
from brain.frontmatter import split_frontmatter
from brain.resolver import space_of_path

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE_PREFIXES = ("```", "~~~")


@dataclass(frozen=True)
class Chunk:
    rel_path: str
    space: str
    heading_path: str  # " > "-joined ancestor headings, "" at document root
    pos: int           # dense ordinal within the file
    text: str


def _normalize_wikilinks(text: str) -> str:
    """Render ``[[Target|alias]]`` as ``alias`` and ``[[Target]]`` as ``Target``
    so keyword search matches what a human would type, not link syntax."""
    def repl(m: re.Match) -> str:
        return m.group(4) if m.group(4) else m.group(1)
    return WIKILINK_RE.sub(repl, text)


def _is_fence(line: str) -> bool:
    s = line.lstrip()
    return s.startswith(_FENCE_PREFIXES)


def _sections(body: str) -> list[tuple[tuple[str, ...], str]]:
    """Split body into (heading_path, text) sections on ATX headings.

    Fence-aware: a ``#`` inside a code fence is content, not a heading. The
    heading line itself stays as the first line of its section's text.
    """
    sections: list[tuple[tuple[str, ...], str]] = []
    stack: list[tuple[int, str]] = []
    cur_lines: list[str] = []
    cur_hp: tuple[str, ...] = ()
    in_fence = False

    def flush() -> None:
        text = "\n".join(cur_lines).strip("\n")
        if text.strip():
            sections.append((cur_hp, text))

    for line in body.split("\n"):
        if _is_fence(line):
            in_fence = not in_fence
            cur_lines.append(line)
            continue
        m = None if in_fence else HEADING_RE.match(line)
        if m:
            flush()
            level, title = len(m.group(1)), m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            cur_hp = tuple(t for _, t in stack)
            cur_lines = [line]
        else:
            cur_lines.append(line)
    flush()
    return sections


def _blocks(text: str) -> list[str]:
    """Paragraph blocks split on blank lines, treating a fenced code region
    (blank lines and all) as a single indivisible block."""
    blocks: list[str] = []
    cur: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        if _is_fence(line):
            in_fence = not in_fence
            cur.append(line)
            continue
        if not in_fence and line.strip() == "":
            if cur:
                blocks.append("\n".join(cur))
                cur = []
        else:
            cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    return blocks


def _split_oversize(text: str, target: int, max_chars: int) -> list[str]:
    """Pack paragraph blocks up to `target`; hard-split any single block that
    exceeds `max_chars`. Every returned piece is <= max_chars."""
    pieces: list[str] = []
    cur = ""
    for b in _blocks(text):
        if len(b) > max_chars:
            if cur:
                pieces.append(cur)
                cur = ""
            for i in range(0, len(b), max_chars):
                pieces.append(b[i:i + max_chars])
            continue
        if cur and len(cur) + 2 + len(b) > target:
            pieces.append(cur)
            cur = b
        else:
            cur = b if not cur else f"{cur}\n\n{b}"
    if cur:
        pieces.append(cur)
    return pieces


def _common_prefix(a: tuple[str, ...], b: tuple[str, ...]) -> tuple[str, ...]:
    out: list[str] = []
    for x, y in zip(a, b):
        if x != y:
            break
        out.append(x)
    return tuple(out)


def chunk_markdown(
    rel_path: str,
    text: str,
    *,
    target_chars: int = 1200,
    max_chars: int = 2400,
    min_chars: int = 64,
) -> list[Chunk]:
    """Chunk a markdown document into heading-aware retrieval units.

    Sections are packed toward `target_chars`, never exceed `max_chars`, and
    sections/chunks under `min_chars` fold into a neighbor so a file of one-line
    headings does not explode into micro-chunks. When sections merge, the
    heading path becomes their longest common ancestor.
    """
    _, body = split_frontmatter(text)
    body = _normalize_wikilinks(body)

    units: list[tuple[tuple[str, ...], str]] = []
    for hp, sec in _sections(body):
        if len(sec) <= max_chars:
            units.append((hp, sec))
        else:
            units.extend((hp, piece) for piece in _split_oversize(sec, target_chars, max_chars))

    # Greedy pack: merge consecutive units while they fit under target AND share
    # a heading ancestor (or are both at document root), so context is not
    # diluted by fusing unrelated top-level sections.
    packed: list[tuple[tuple[str, ...], str]] = []
    buf_hp: tuple[str, ...] | None = None
    buf = ""
    for hp, t in units:
        fits = buf and len(buf) + 2 + len(t) <= target_chars
        related = buf_hp is not None and (_common_prefix(buf_hp, hp) or (not buf_hp and not hp))
        if fits and related:
            buf = f"{buf}\n\n{t}"
            buf_hp = _common_prefix(buf_hp, hp)
        else:
            if buf:
                packed.append((buf_hp, buf))
            buf_hp, buf = hp, t
    if buf:
        packed.append((buf_hp, buf))

    # Fold away stray sub-min_chars chunks into their neighbor.
    folded: list[tuple[tuple[str, ...], str]] = []
    for hp, t in packed:
        if folded and (len(t) < min_chars or len(folded[-1][1]) < min_chars):
            php, pt = folded[-1]
            folded[-1] = (_common_prefix(php, hp), f"{pt}\n\n{t}")
        else:
            folded.append((hp, t))

    space = space_of_path(rel_path) or ""
    return [
        Chunk(rel_path=rel_path, space=space, heading_path=" > ".join(hp), pos=i, text=t.strip("\n"))
        for i, (hp, t) in enumerate(folded)
        if t.strip()
    ]


def embedding_input(chunk: Chunk) -> str:
    """Text actually embedded: title + heading path prepended so the vector
    captures where the chunk sits, then the chunk body."""
    title = PurePosixPath(chunk.rel_path).stem
    prefix = f"{title} — {chunk.heading_path}" if chunk.heading_path else title
    return f"{prefix}\n\n{chunk.text}"
